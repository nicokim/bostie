import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import msgspec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from bostie import ASGIApp, Bot, Context
from bostie.events import ApplicationAuthorized
from bostie.testing import FakeDiscord, payloads, run

KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEY = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
WEBHOOK_EVENTS = Path(__file__).parent / "fixtures" / "webhook_events"

type Message = dict[str, Any]


async def post(app: ASGIApp, body: bytes, *, signed: bool = True) -> tuple[int, bytes]:
    timestamp = b"1700000000"
    signature = KEY.sign(timestamp + body if signed else b"forged").hex().encode()
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"x-signature-ed25519", signature),
            (b"x-signature-timestamp", timestamp),
        ],
    }
    requests = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[Message] = []

    async def receive() -> Message:
        return requests.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start, answer = sent
    return start["status"], answer["body"]


@contextlib.asynccontextmanager
async def serving(app: ASGIApp) -> AsyncIterator[None]:
    inbox: asyncio.Queue[Message] = asyncio.Queue()
    outbox: asyncio.Queue[Message] = asyncio.Queue()
    lifespan = asyncio.create_task(app({"type": "lifespan"}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
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
