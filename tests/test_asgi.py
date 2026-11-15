import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import msgspec
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from bostie import ASGIApp, Bot, Context
from bostie.asgi import _MAX_BODY
from bostie.events import ApplicationAuthorized, Starting, Stopped
from bostie.testing import FakeDiscord, payloads, run, running

KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEY = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
WEBHOOK_EVENTS = Path(__file__).parent / "fixtures" / "webhook_events"

type Message = dict[str, Any]


async def post(
    app: ASGIApp, *chunks: bytes, signed: bool = True, method: str = "POST"
) -> tuple[int, bytes]:
    timestamp = b"1700000000"
    body = b"".join(chunks)
    signature = KEY.sign(timestamp + body if signed else b"forged").hex().encode()
    scope = {
        "type": "http",
        "method": method,
        "headers": [
            (b"x-signature-ed25519", signature),
            (b"x-signature-timestamp", timestamp),
        ],
    }
    requests = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return requests.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start, answer = sent
    return start["status"], answer["body"]


type Queue = asyncio.Queue[Message]


async def start_up(app: ASGIApp) -> tuple[Message, asyncio.Task[None], Queue, Queue]:
    inbox: Queue = asyncio.Queue()
    outbox: Queue = asyncio.Queue()
    lifespan = asyncio.create_task(app({"type": "lifespan"}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    return await outbox.get(), lifespan, inbox, outbox


@contextlib.asynccontextmanager
async def serving(app: ASGIApp) -> AsyncIterator[None]:
    started, lifespan, inbox, outbox = await start_up(app)
    assert started["type"] == "lifespan.startup.complete"
    try:
        yield
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        assert (await outbox.get())["type"] == "lifespan.shutdown.complete"
        await lifespan


def test_signed_interactions_reach_commands_without_a_gateway() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.command
        async def ping(ctx: Context) -> None:
            """Answer pong."""
            await ctx.respond("pong")

        app = ASGIApp(bot, token=fake.token, public_key=PUBLIC_KEY)
        interaction = payloads.interaction(2, payloads.command("ping"))
        body = msgspec.json.encode(interaction)
        async with serving(app):
            assert bot.application_id == fake.application_id
            assert await post(app, body, signed=False) == (401, b"")
            ping_body = msgspec.json.encode(interaction | {"type": 1})
            assert await post(app, ping_body) == (200, b'{"type":1}')
            callback = fake.expect_callback(interaction)
            assert await post(app, body) == (202, b"")
            assert (await callback).json["data"]["content"] == "pong"
        assert fake.connections == []
        assert fake.commands[0]["name"] == "ping"

    run(scenario)


def test_http_mode_runs_tasks_between_starting_and_stopped() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        seen: list[str] = []
        ticked = asyncio.Event()

        @bot.listen
        async def set_up(event: Starting) -> None:
            seen.append("starting")

        @bot.listen
        async def tear_down(event: Stopped) -> None:
            seen.append("stopped")

        @bot.every(seconds=60)
        async def tick() -> None:
            ticked.set()

        async with serving(ASGIApp(bot, token=fake.token, public_key=PUBLIC_KEY)):
            await ticked.wait()
            assert not bot.is_ready

        assert seen == ["starting", "stopped"]

    run(scenario)


def test_webhook_events_reach_listeners_with_the_apps_own_key() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, auto_sync=False)
        application = payloads.fixture("rest/application_me")
        fake.fail(
            "GET", "/applications/@me", 200, application | {"verify_key": PUBLIC_KEY}
        )
        app = ASGIApp(bot, token=fake.token)
        async with serving(app), bot.stream(ApplicationAuthorized) as authorized:
            ping = (WEBHOOK_EVENTS / "PING.json").read_bytes()
            assert await post(app, ping) == (204, b"")
            event = (WEBHOOK_EVENTS / "APPLICATION_AUTHORIZED.json").read_bytes()
            assert await post(app, event) == (204, b"")
            installed = await anext(authorized)
        assert installed.scopes == ("applications.commands",)

    run(scenario)


@pytest.mark.parametrize(
    ("method", "chunks", "status"),
    [("GET", [b"{}"], 405), ("POST", [b" " * _MAX_BODY, b" "], 413)],
)
def test_the_endpoint_refuses_other_methods_and_bodies_over_4_mib(
    method: str, chunks: list[bytes], status: int
) -> None:
    async def main() -> None:
        app = ASGIApp(Bot(), public_key=PUBLIC_KEY)
        assert await post(app, *chunks, method=method) == (status, b"")

    asyncio.run(main())


def test_a_failed_startup_is_reported_to_the_server() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        started, lifespan, _, _ = await start_up(ASGIApp(bot, token="wrong"))
        await lifespan
        assert started == {
            "type": "lifespan.startup.failed",
            "message": "401: Unauthorized",
        }

    run(scenario)


def test_on_a_running_bot_the_app_reads_the_key_and_leaves_the_bot_running() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, auto_sync=False)
        application = payloads.fixture("rest/application_me")
        async with running(bot, fake):
            fake.fail(
                "GET",
                "/applications/@me",
                200,
                application | {"verify_key": PUBLIC_KEY},
            )
            app = ASGIApp(bot)
            async with serving(app), bot.stream(ApplicationAuthorized) as authorized:
                event = (WEBHOOK_EVENTS / "APPLICATION_AUTHORIZED.json").read_bytes()
                assert await post(app, event) == (204, b"")
                await anext(authorized)
            assert bot.is_ready

    run(scenario)
