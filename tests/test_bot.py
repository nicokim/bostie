import asyncio
import contextlib
import logging
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Annotated, Any, Literal

import pytest
from fakediscord import FakeDiscord, payloads

from lyrecord import (
    AutocompleteContext,
    Bot,
    CommandSyncError,
    ComponentContext,
    Context,
    File,
    Forbidden,
    HTTPException,
    Intents,
    InteractionExpired,
    InvalidToken,
    Member,
    Message,
    NotFound,
    Option,
    OptionType,
    Permissions,
    PrivilegedIntentsRequired,
    Range,
    Router,
    ui,
)
from lyrecord.context import ResponseState
from lyrecord.events import (
    ChannelUpdate,
    GuildCreate,
    InteractionCreate,
    MemberJoin,
    MessageCreate,
    MessageDelete,
    RawEvent,
    Ready,
)
from lyrecord.gateway import GatewayClient

type Scenario = Callable[[FakeDiscord], Coroutine[Any, Any, None]]

STRING = OptionType.STRING
INTEGER = OptionType.INTEGER
USER = OptionType.USER
MENTIONS = {"parse": ["users", "roles"]}
EPHEMERAL = 64
MEMBER_ID = "820774541721731073"


class Unit(Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"


def run(scenario: Scenario, **options: Any) -> None:
    async def main() -> None:
        async with FakeDiscord(**options) as fake, asyncio.timeout(10):
            await scenario(fake)

    asyncio.run(main())


@contextlib.asynccontextmanager
async def running(bot: Bot, fake: FakeDiscord) -> AsyncIterator[None]:
    started = asyncio.create_task(bot.start(fake.token))
    ready = asyncio.create_task(bot.wait_until_ready())
    await asyncio.wait([started, ready], return_when=asyncio.FIRST_COMPLETED)
    ready.cancel()
    if started.done():
        await started
    try:
        yield
    finally:
        await bot.close()
        await started


@contextlib.contextmanager
def fake_in_thread() -> Iterator[FakeDiscord]:
    serving = threading.Event()
    fakes: list[FakeDiscord] = []
    stops: list[Callable[[], object]] = []

    async def serve() -> None:
        stop = asyncio.Event()
        async with FakeDiscord() as fake:
            loop = asyncio.get_running_loop()
            fakes.append(fake)
            stops.append(lambda: loop.call_soon_threadsafe(stop.set))
            serving.set()
            await stop.wait()

    server = threading.Thread(target=asyncio.run, args=(serve(),))
    server.start()
    assert serving.wait(10)
    try:
        yield fakes[0]
    finally:
        stops[0]()
        server.join(10)


async def sent_op(fake: FakeDiscord, op: int) -> Any:
    while (payload := await fake.gateway_received.get())["op"] != op:
        pass
    return payload["d"]


def example_bot(fake: FakeDiscord, **options: Any) -> Bot:
    bot = Bot(api_url=fake.api_url, **options)

    @bot.command
    async def roll(
        ctx: Context,
        sides: Annotated[Range[int, 2, 100], "Sides per die"] = 6,
        count: Annotated[int, Option(min=1, max=10, description="How many dice")] = 1,
        mode: Literal["sum", "list"] = "sum",
        unit: Unit = Unit.METRIC,
    ) -> None:
        """Roll some dice."""
        await ctx.respond(f"{count}d{sides} in {mode} mode ({unit.value})")

    async def tag_names(ctx: AutocompleteContext, value: str) -> list[str]:
        return [f"{value}-{index}" for index in range(30)]

    @bot.on_component("vote:{tag}:{up:int}")
    async def vote(ctx: ComponentContext, tag: str, up: int) -> None:
        await ctx.update(f"{tag} {up!r}")

    @bot.command
    async def tag(
        ctx: Context, name: Annotated[str, Option(autocomplete=tag_names)]
    ) -> None:
        """Show a tag."""
        button = ui.button("Helpful", custom_id=vote.id(tag=name, up=1))
        await ctx.respond(name, components=[ui.row(button)])

    role = bot.group(
        "role",
        "Manage roles",
        default_permissions=Permissions.MANAGE_ROLES,
        guild_only=True,
    )
    color = role.group("color", "Color roles")

    @color.command(name="set")
    async def color_set(ctx: Context, member: Member, hex: Range[str, 7, 7]) -> None:
        """Give a member a color role."""
        await ctx.respond(f"{member.mention} is now {hex}")

    @bot.command(ephemeral=True)
    async def report(ctx: Context) -> None:
        """Upload the last report."""
        await ctx.respond("Here it is:", files=[File("report.txt", b"all good")])

    return bot


def test_autosync_puts_only_changed_commands_and_an_explicit_sync_always_puts() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        route = f"/applications/{fake.application_id}/commands"
        presence: dict[str, Any] = {
            "since": None,
            "activities": [],
            "status": "idle",
            "afk": False,
        }
        bot = example_bot(
            fake,
            intents=Intents.DEFAULT | Intents.MESSAGE_CONTENT,
            presence=presence,
        )
        async with running(bot, fake):
            identify = await fake.next_identify()
        first = [(request.method, request.path) for request in fake.requests]
        assert first == [
            ("GET", "/gateway/bot"),
            ("GET", "/applications/@me"),
            ("GET", route),
            ("PUT", route),
        ]
        assert fake.requests[2].query == {"with_localizations": "true"}
        assert [command["name"] for command in fake.requests[3].json] == [
            "roll",
            "tag",
            "role",
            "report",
        ]
        assert bot.application_id == fake.application_id
        assert bot.user is not None and bot.user.id == int(fake.bot_user["id"])
        assert identify["intents"] == Intents.DEFAULT | Intents.MESSAGE_CONTENT
        assert "capabilities" not in identify
        assert identify["presence"] == presence

        fake.requests.clear()
        again = example_bot(fake)
        async with running(again, fake):
            assert fake.put_count == 1
            await again.sync_commands()
        requests = [(request.method, request.path) for request in fake.requests]
        assert requests == first[:3] + first[2:]
        assert fake.put_count == 2

    run(scenario)


def test_startup_warns_about_listeners_whose_privileged_intent_is_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.listen
        async def greet(event: MemberJoin) -> None:
            pass

        async with running(bot, fake):
            pass

    run(scenario)

    warnings = [record for record in caplog.records if "intent" in record.message]
    assert len(warnings) == 1
    assert "greet listens to GUILD_MEMBER_ADD" in warnings[0].message
    assert "GUILD_MEMBERS" in warnings[0].message


def test_a_slash_command_gets_its_typed_options_and_defaults() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with running(example_bot(fake), fake):
            callback = await fake.interaction(
                2,
                payloads.command(
                    "roll",
                    payloads.option("sides", INTEGER, 20),
                    payloads.option("mode", STRING, "list"),
                    payloads.option("unit", STRING, "imperial"),
                ),
            )

        assert callback.query == {"with_response": "true"}
        assert callback.json == {
            "type": 4,
            "data": {
                "content": "1d20 in list mode (imperial)",
                "allowed_mentions": MENTIONS,
            },
        }

    run(scenario)


def test_autocomplete_answers_with_at_most_25_choices() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with running(example_bot(fake), fake):
            callback = await fake.interaction(
                4,
                payloads.command(
                    "tag", payloads.option("name", STRING, "ru", focused=True)
                ),
            )

        choices = [
            {"name": f"ru-{index}", "value": f"ru-{index}"} for index in range(25)
        ]
        assert callback.json == {"type": 8, "data": {"choices": choices}}

    with pytest.warns(UserWarning, match="more than 25 choices"):
        run(scenario)


def test_a_subcommand_in_a_group_gets_its_resolved_member() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        set_color = payloads.subcommand(
            "set",
            payloads.option("member", USER, MEMBER_ID),
            payloads.option("hex", STRING, "#ff8800"),
        )
        data = payloads.command(
            "role", payloads.group("color", set_color), resolved=payloads.resolved()
        )
        async with running(example_bot(fake), fake):
            callback = await fake.interaction(2, data)

        assert callback.json["data"]["content"] == f"<@{MEMBER_ID}> is now #ff8800"

    run(scenario)


def test_a_slow_handler_is_deferred_after_the_delay_then_edits_the_answer() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        loop = asyncio.get_running_loop()
        bot = Bot(api_url=fake.api_url, auto_defer=0.2)
        release = asyncio.Event()
        answered: asyncio.Future[Message | None] = loop.create_future()

        @bot.command
        async def slow(ctx: Context) -> None:
            """Take a while."""
            await release.wait()
            answered.set_result(await ctx.respond("done"))

        @bot.command(auto_defer=None)
        async def patient(ctx: Context) -> None:
            """Take a while without deferring."""
            await asyncio.sleep(0.4)
            await ctx.respond("done")

        async with running(bot, fake):
            undeferred = await fake.interaction(2, payloads.command("patient"))
            sent = loop.time()
            callback = await fake.interaction(2, payloads.command("slow"))
            deferred_after = loop.time() - sent
            release.set()
            message = await answered

        assert undeferred.json["type"] == 4
        assert callback.json == {"type": 5}
        resolution = time.get_clock_info("monotonic").resolution
        assert 0.2 - resolution <= deferred_after < 1.0
        assert message is not None and message.content == "done"
        edit = fake.requests[-1]
        token = callback.path.split("/")[3]
        original = f"/webhooks/{fake.application_id}/{token}"
        assert (edit.method, edit.path) == ("PATCH", f"{original}/messages/@original")

    run(scenario)


def test_a_handler_that_starts_late_is_deferred_at_once_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def block_the_loop(event: InteractionCreate) -> bool:
        time.sleep(1.1)
        return True

    async def scenario(fake: FakeDiscord) -> None:
        loop = asyncio.get_running_loop()
        bot = Bot(api_url=fake.api_url, auto_defer=0.5)

        @bot.command
        async def stuck(ctx: Context) -> None:
            """Never answer."""
            await asyncio.Event().wait()

        async with running(bot, fake):
            sent = loop.time()
            _, callback = await asyncio.gather(
                bot.wait_for(InteractionCreate, check=block_the_loop),
                fake.interaction(2, payloads.command("stuck")),
            )
            elapsed = loop.time() - sent

        assert callback.json == {"type": 5}
        assert elapsed < 1.4

    run(scenario)

    (warning,) = [
        record for record in caplog.records if "reached its handler" in record.message
    ]
    assert warning.levelno == logging.WARNING


def test_only_slash_interactions_run_a_command_and_unanswered_ones_are_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        handled: list[int] = []
        seen: asyncio.Queue[int] = asyncio.Queue()
        deferred: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        @bot.command
        async def silent(ctx: Context) -> None:
            """Answer nothing."""
            handled.append(ctx.interaction.id)

        @bot.command
        async def thinking(ctx: Context) -> None:
            """Defer, then stop."""
            await ctx.defer()
            deferred.set_result(None)

        @bot.listen
        async def on_interaction(event: InteractionCreate) -> None:
            seen.put_nowait(event.interaction.id)

        async with running(bot, fake):
            sent: list[int] = []
            for command_type in (2, 3, 1):
                data = payloads.command("silent") | {"type": command_type}
                interaction = payloads.interaction(2, data)
                sent.append(int(interaction["id"]))
                await fake.dispatch("INTERACTION_CREATE", interaction)
                assert await seen.get() == sent[-1]
            await fake.interaction(2, payloads.command("thinking"))
            await deferred

        assert handled == sent[2:]

    run(scenario)

    warnings = [
        record.message
        for record in caplog.records
        if "without answering" in record.message
    ]
    assert len(warnings) == 2
    assert ".silent returned without answering" in warnings[0]
    assert ".thinking returned after deferring without answering" in warnings[1]


def test_buttons_and_modal_submits_are_routed_by_custom_id_template() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = example_bot(fake)
        submitted: list[tuple[int, Any]] = []

        @bot.on_component("feedback:{ticket:int}")
        async def feedback(ctx: ComponentContext, ticket: int) -> None:
            submitted.append((ticket, ctx.interaction.raw["data"]["components"]))
            await ctx.respond("Thanks!", ephemeral=True)

        rows = [
            {"type": 1, "components": [{"type": 4, "custom_id": "text", "value": "ok"}]}
        ]
        async with running(bot, fake):
            clicked = await fake.interaction(3, payloads.button("vote:rules:1"))
            modal = await fake.interaction(
                5, {"custom_id": "feedback:7", "components": rows}
            )

        assert clicked.json == {
            "type": 7,
            "data": {"content": "rules 1", "allowed_mentions": MENTIONS},
        }
        assert modal.json["type"] == 4
        assert submitted == [(7, rows)]

    run(scenario)


def test_a_bot_with_only_component_routes_answers_clicks() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.on_component("go")
        async def go(ctx: ComponentContext) -> None:
            await ctx.update("went")

        async with running(bot, fake):
            clicked = await fake.interaction(3, payloads.button("go"))

        assert clicked.json["type"] == 7

    run(scenario)


def test_a_handler_that_closes_the_bot_finishes_the_shutdown() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.command
        async def stop(ctx: Context) -> None:
            """Stop the bot."""
            await ctx.respond("bye")
            await bot.close()

        started = asyncio.create_task(bot.start(fake.token))
        await bot.wait_until_ready()
        answered = await fake.interaction(2, payloads.command("stop"))

        await started
        assert answered.json["type"] == 4
        assert fake.connections[-1].close_code == 1000

    run(scenario)


def test_listeners_the_cache_and_wait_for_see_gateway_events() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        messages: asyncio.Queue[Message] = asyncio.Queue()

        @bot.listen
        async def on_message(event: MessageCreate) -> None:
            messages.put_nowait(event.message)

        async with running(bot, fake):
            guild = payloads.guild()
            created, _ = await asyncio.gather(
                bot.wait_for(GuildCreate), fake.dispatch("GUILD_CREATE", guild)
            )
            cached_guild = bot.cache.guild(int(guild["id"]))
            assert cached_guild is not None and cached_guild.name == guild["name"]
            assert created.guild.id == cached_guild.id

            hidden = payloads.obfuscated_channel()
            await asyncio.gather(
                bot.wait_for(ChannelUpdate), fake.dispatch("CHANNEL_UPDATE", hidden)
            )
            channel = bot.cache.channel(int(hidden["id"]))
            assert channel is not None and channel.obfuscated
            assert (channel.name, channel.raw_json) == ("___hidden___", None)

            message = payloads.message()
            with pytest.raises(ZeroDivisionError):
                await asyncio.gather(
                    bot.wait_for(MessageCreate, check=lambda event: 1 / 0 > 0),
                    fake.dispatch("MESSAGE_CREATE", message),
                )
            received = await messages.get()
            assert (received.id, received.content) == (
                int(message["id"]),
                message["content"],
            )

            first, second = (
                {"id": str(number), "channel_id": "1"} for number in (1, 2)
            )
            deleted, *_ = await asyncio.gather(
                bot.wait_for(MessageDelete, check=lambda event: event.id == 2),
                fake.dispatch("MESSAGE_DELETE", first),
                fake.dispatch("MESSAGE_DELETE", second),
            )
            assert deleted.id == 2

    run(scenario)


def test_the_dispatch_filter_follows_listeners_waiters_and_the_cache_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filters: list[list[str] | None] = []
    send_filter = GatewayClient.filter

    def record(gateway: GatewayClient, names: Iterable[str] | None) -> None:
        filters.append(None if names is None else list(names))
        send_filter(gateway, names)

    monkeypatch.setattr(GatewayClient, "filter", record)

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, cache=False)
        plugin = Router()
        bot.include(plugin)
        deleted: asyncio.Queue[int] = asyncio.Queue()
        async with running(bot, fake):
            assert filters[-1] == ["RATE_LIMITED"]
            created, _ = await asyncio.gather(
                bot.wait_for(GuildCreate),
                fake.dispatch("GUILD_CREATE", payloads.guild()),
            )
            assert bot.cache.guild(created.guild.id) is None
            assert filters[-2:] == [["GUILD_CREATE", "RATE_LIMITED"], ["RATE_LIMITED"]]

            @plugin.listen
            async def on_delete(event: MessageDelete) -> None:
                deleted.put_nowait(event.id)

            assert filters[-1] == ["MESSAGE_DELETE", "RATE_LIMITED"]
            await fake.dispatch("MESSAGE_DELETE", {"id": "3", "channel_id": "1"})
            assert await deleted.get() == 3

    run(scenario)


def test_close_during_startup_makes_start_return() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        started = asyncio.create_task(bot.start(fake.token))
        await bot.close()
        await started

        fake.latency(0.3)
        started = asyncio.create_task(bot.start(fake.token))
        await asyncio.sleep(0.1)
        await bot.close()
        await started
        assert not fake.connections

    run(scenario)


def test_raw_listeners_receive_events_lyrecord_does_not_model() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        named: asyncio.Queue[RawEvent] = asyncio.Queue()
        everything: asyncio.Queue[RawEvent] = asyncio.Queue()

        @bot.listen_raw("BRAND_NEW_EVENT")
        async def brand_new(event: RawEvent) -> None:
            named.put_nowait(event)

        @bot.listen
        async def catch_all(event: RawEvent) -> None:
            everything.put_nowait(event)

        async with running(bot, fake):
            seq = await fake.dispatch("BRAND_NEW_EVENT", {"answer": 42})
            event = await named.get()
            assert (event.name, event.seq, event.raw) == (
                "BRAND_NEW_EVENT",
                seq,
                {"answer": 42},
            )
            await fake.dispatch("TYPING_START", {"channel_id": "1", "user_id": "2"})
            names = [(await everything.get()).name for _ in range(3)]
            assert names == ["READY", "BRAND_NEW_EVENT", "TYPING_START"]

    run(scenario)


def test_a_resume_replays_missed_events_once_and_skips_stale_interactions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        handled: list[int] = []
        messages: asyncio.Queue[int] = asyncio.Queue()
        interactions: asyncio.Queue[int] = asyncio.Queue()

        @bot.command
        async def ping(ctx: Context) -> None:
            """Ping."""
            handled.append(ctx.interaction.id)
            await ctx.respond("pong")

        @bot.listen
        async def on_message(event: MessageCreate) -> None:
            messages.put_nowait(event.message.id)

        @bot.listen
        async def on_interaction(event: InteractionCreate) -> None:
            interactions.put_nowait(event.interaction.id)

        minute_ago = time.time() - 60
        async with running(bot, fake):
            await fake.send_op(7)
            missed = payloads.message()
            missed_seq = await fake.dispatch("MESSAGE_CREATE", missed)
            stale = payloads.interaction(
                2, payloads.command("ping"), id=payloads.snowflake(minute_ago)
            )
            await fake.dispatch("INTERACTION_CREATE", stale)

            resume = await fake.next_resume()
            assert resume["seq"] == missed_seq - 1
            assert fake.connections[-1].path == "/gateway-resume"
            assert await messages.get() == int(missed["id"])
            assert await interactions.get() == int(stale["id"])

            late = await fake.interaction(
                2, payloads.command("ping"), id=payloads.snowflake(minute_ago)
            )
            marker = payloads.message()
            await fake.dispatch("MESSAGE_CREATE", marker)
            assert await messages.get() == int(marker["id"])

        assert handled == [int(late.path.split("/")[2])]
        sent = [
            fake.gateway_received.get_nowait()
            for _ in range(fake.gateway_received.qsize())
        ]
        assert [payload["op"] for payload in sent].count(2) == 1

    run(scenario)

    replayed = [record for record in caplog.records if "replayed" in record.message]
    assert [record.levelno for record in replayed] == [logging.WARNING]


def test_wait_until_ready_raises_once_the_bot_stops_before_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        monkeypatch.delenv("LYRECORD_TOKEN", raising=False)
        bot = Bot(api_url=fake.api_url)
        failures: list[tuple[str | None, type[Exception]]] = [
            (None, ValueError),
            ("wrong-token", InvalidToken),
        ]
        for token, error in failures:
            started = asyncio.create_task(bot.start(token))
            with pytest.raises(RuntimeError, match="stopped before READY"):
                await bot.wait_until_ready()
            with pytest.raises(error):
                await started

        fake.latency(0.3)
        started = asyncio.create_task(bot.start(fake.token))
        waiting = asyncio.create_task(bot.wait_until_ready())
        await asyncio.sleep(0.1)
        await bot.close()
        await started
        with pytest.raises(RuntimeError, match="stopped before READY"):
            await waiting

        fake.latency(0)
        async with running(bot, fake):
            await bot.wait_until_ready()

        first = asyncio.create_task(bot.start(fake.token))
        await bot.wait_until_ready()
        await bot.close()
        second = asyncio.create_task(bot.start(fake.token))
        await bot.wait_until_ready()
        await first
        await bot.close()
        await second

    run(scenario)


def test_start_seeds_the_daily_identify_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        exhausted = {"total": 1000, "remaining": 0, "reset_after": 300}
        gateway_bot = payloads.fixture("rest/gateway_bot")
        fake.fail(
            "GET",
            "/gateway/bot",
            200,
            gateway_bot | {"url": fake.gateway_url, "session_start_limit": exhausted},
        )
        async with running(Bot(api_url=fake.api_url), fake):
            await fake.next_identify()

    run(scenario)

    assert any(
        "daily identify limit reached" in record.getMessage()
        for record in caplog.records
    )


def test_a_replayed_interaction_is_deferred_by_its_age_not_its_receipt() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, auto_defer=1.0)
        states: asyncio.Queue[ResponseState] = asyncio.Queue()

        @bot.command
        async def slow(ctx: Context) -> None:
            """Answer slowly."""
            await asyncio.sleep(0.5)
            states.put_nowait(ctx.state)
            await ctx.respond("done")

        async with running(bot, fake):
            await fake.send_op(7)
            created = payloads.snowflake(time.time() - 2.5)
            first = await fake.interaction(2, payloads.command("slow"), id=created)
            await fake.next_resume()
            assert await states.get() is ResponseState.DEFERRED

        assert first.json["type"] == 5

    run(scenario)


def test_start_resolves_the_token_and_raises_fatal_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        with pytest.raises(RuntimeError, match="started"):
            _ = bot.http
        monkeypatch.delenv("LYRECORD_TOKEN", raising=False)
        with pytest.raises(ValueError, match="LYRECORD_TOKEN"):
            await bot.start()

        monkeypatch.setenv("LYRECORD_TOKEN", f"Bot {fake.token}")
        started = asyncio.create_task(bot.start())
        await fake.next_identify()
        await fake.close_gateway(4014)
        with pytest.raises(PrivilegedIntentsRequired):
            await started

        with pytest.raises(InvalidToken):
            await Bot(api_url=fake.api_url).start("wrong-token")
        assert fake.requests[-1].path == "/gateway/bot"

    run(scenario)


def test_rest_errors_become_typed_exceptions_and_a_global_429_is_waited_out() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        channel_id = int(payloads.channel()["id"])
        path = f"/channels/{channel_id}"
        async with running(bot, fake):
            fake.fail("GET", path, 403, payloads.fixture("rest/error_403"))
            with pytest.raises(Forbidden) as raised:
                await bot.fetch_channel(channel_id)
            assert (raised.value.status, raised.value.code, raised.value.message) == (
                403,
                50013,
                "Missing Permissions",
            )

            fake.rate_limit("GET", path, scope="global", retry_after=0.2)
            channel = await bot.fetch_channel(channel_id)
            assert channel.id == channel_id
            assert [request.path for request in fake.requests[-2:]] == [path, path]

    run(scenario)


def test_a_rejected_command_put_raises_an_error_naming_the_function() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.command
        async def dice(ctx: Context, sides: int = 6, count: int = 1) -> None:
            """Roll dice."""

        error = {
            "code": 50035,
            "message": "Invalid Form Body",
            "errors": {
                "0": {
                    "options": {
                        "1": {
                            "description": {
                                "_errors": [
                                    {
                                        "code": "BASE_TYPE_BAD_LENGTH",
                                        "message": "Must be 100 or fewer in length.",
                                    }
                                ]
                            }
                        }
                    }
                }
            },
        }
        fake.fail("PUT", f"/applications/{fake.application_id}/commands", 400, error)
        with pytest.raises(CommandSyncError) as raised:
            await bot.start(fake.token)

        assert (
            raised.value.command == f"/dice ({__name__}.{dice.callback.__qualname__})"
        )
        assert raised.value.option_path == "option 'count'"
        assert raised.value.discord_message == (
            "description: Must be 100 or fewer in length."
        )
        assert not fake.connections

    run(scenario)


def test_error_hooks_run_from_the_owning_router_upward_then_the_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        admin = Router()
        hooks: list[str] = []
        channel_id = int(payloads.channel()["id"])

        @admin.on_error(Forbidden)
        async def pass_up(ctx: Context, error: Forbidden) -> None:
            hooks.append("admin")
            raise error

        @bot.on_error(HTTPException)
        async def explain(ctx: Context, error: HTTPException) -> None:
            hooks.append("bot")
            await ctx.respond(f"Discord said {error.code}", ephemeral=True)

        @admin.command
        async def purge(ctx: Context) -> None:
            """Purge a channel."""
            await bot.fetch_channel(channel_id)

        @bot.command
        async def crash(ctx: Context) -> None:
            """Crash."""
            raise RuntimeError("boom")

        @bot.command
        async def defer_and_fail(ctx: Context) -> None:
            """Fail after deferring."""
            await ctx.defer()
            failing.set_result(ctx)
            raise RuntimeError("late boom")

        async def broken(ctx: AutocompleteContext, value: str) -> list[str]:
            raise RuntimeError("autocomplete boom")

        @bot.command
        async def lookup(
            ctx: Context, query: Annotated[str, Option(autocomplete=broken)]
        ) -> None:
            """Look something up."""

        bot.include(admin)
        failing: asyncio.Future[Context] = asyncio.get_running_loop().create_future()
        async with running(bot, fake):
            fake.fail(
                "GET",
                f"/channels/{channel_id}",
                403,
                payloads.fixture("rest/error_403"),
            )
            explained = await fake.interaction(2, payloads.command("purge"))
            crashed = await fake.interaction(2, payloads.command("crash"))
            await fake.interaction(2, payloads.command("defer_and_fail"))
            await (await failing).followup("after the failure answer")
            focused = payloads.option("query", STRING, "x", focused=True)
            suggested = await fake.interaction(4, payloads.command("lookup", focused))

        assert hooks == ["admin", "bot"]
        assert explained.json["data"] == {
            "content": "Discord said 50013",
            "allowed_mentions": MENTIONS,
            "flags": EPHEMERAL,
        }
        assert crashed.json["data"] == {
            "content": "Something went wrong.",
            "allowed_mentions": MENTIONS,
            "flags": EPHEMERAL,
        }
        (edit,) = [request for request in fake.requests if request.method == "PATCH"]
        assert edit.path.endswith("/messages/@original")
        assert edit.json == {
            "content": "Something went wrong.",
            "allowed_mentions": MENTIONS,
        }
        assert suggested.json == {"type": 8, "data": {"choices": []}}

    run(scenario)

    (logged,) = [
        record for record in caplog.records if "crash raised" in record.message
    ]
    assert logged.exc_info is not None
    assert str(logged.exc_info[1]) == "boom"


def test_a_dead_interaction_token_is_never_used_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, auto_defer=0.05)
        loop = asyncio.get_running_loop()
        finished: asyncio.Future[ResponseState] = loop.create_future()

        @bot.command
        async def late(ctx: Context) -> None:
            """Answer after the token died."""
            await asyncio.sleep(0.2)
            try:
                await ctx.respond("too late")
            finally:
                finished.set_result(ctx.state)

        unknown = {"message": "Unknown interaction", "code": 10062}
        async with running(bot, fake):
            fake.fail("POST", "/interactions/", 404, unknown, times=3)
            interaction = payloads.interaction(2, payloads.command("late"))
            await fake.dispatch("INTERACTION_CREATE", interaction)
            assert await finished is ResponseState.EXPIRED

        sent = [
            (request.method, request.json)
            for request in fake.requests
            if interaction["token"] in request.path
        ]
        assert sent == [("POST", {"type": 5})]

    run(scenario)

    (logged,) = [record for record in caplog.records if "late raised" in record.message]
    assert logged.exc_info is not None
    assert isinstance(logged.exc_info[1], InteractionExpired)


def test_the_global_rate_limit_reaches_the_rust_limiter() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, global_rate_limit=0)

        with pytest.raises(ValueError, match="zero"):
            await bot.start(fake.token)
        assert fake.requests == []

    run(scenario)


def test_a_response_with_files_is_sent_as_multipart() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with running(example_bot(fake), fake):
            callback = await fake.interaction(2, payloads.command("report"))

        assert [(part.name, part.filename) for part in callback.parts] == [
            ("payload_json", None),
            ("files[0]", "report.txt"),
        ]
        assert callback.parts[1].data == b"all good"
        assert callback.json == {
            "type": 4,
            "data": {
                "content": "Here it is:",
                "allowed_mentions": MENTIONS,
                "attachments": [{"id": 0, "filename": "report.txt"}],
                "flags": EPHEMERAL,
            },
        }

    run(scenario)


def test_the_typed_rest_helpers_send_edit_fetch_and_delete() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        guild_id = int(payloads.fixture("rest/guild")["id"])
        channel_id = int(payloads.channel()["id"])
        member_id = int(payloads.member()["user"]["id"])
        async with running(bot, fake):
            await asyncio.gather(
                bot.wait_for(GuildCreate),
                fake.dispatch("GUILD_CREATE", payloads.guild()),
            )
            sent = await bot.send_message(
                channel_id, "hello", files=[File("a.txt", b"A")], reply_to=5
            )
            assert fake.requests[-1].json == {
                "content": "hello",
                "allowed_mentions": MENTIONS,
                "message_reference": {"type": 0, "message_id": 5},
                "attachments": [{"id": 0, "filename": "a.txt"}],
            }
            assert sent.content == "hello" and sent.raw["id"] == str(sent.id)

            edited = await bot.edit_message(
                channel_id, sent.id, "edited", attachments=[7]
            )
            assert fake.requests[-1].json == {
                "content": "edited",
                "allowed_mentions": MENTIONS,
                "attachments": [{"id": 7}],
            }
            fetched = await bot.fetch_message(channel_id, sent.id)
            assert (edited.content, fetched.content) == ("edited", "edited")
            link = f"https://discord.com/channels/{guild_id}/{channel_id}/{sent.id}"
            assert sent.jump_url == edited.jump_url == fetched.jump_url == link

            await bot.delete_message(channel_id, sent.id, reason="cleanup")
            assert fake.requests[-1].headers["x-audit-log-reason"] == "cleanup"
            with pytest.raises(NotFound):
                await bot.fetch_message(channel_id, sent.id)

            assert (await bot.fetch_channel(channel_id)).id == channel_id
            assert (await bot.fetch_guild(guild_id)).id == guild_id
            assert (await bot.fetch_user(member_id)).id == member_id
            member = await bot.fetch_member(guild_id, member_id)
            assert member.id == member_id

    run(scenario)


def test_gateway_commands_rate_limits_and_latency_reach_the_bot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        limits: asyncio.Queue[RawEvent] = asyncio.Queue()

        @bot.listen_raw("RATE_LIMITED")
        async def on_rate_limit(event: RawEvent) -> None:
            limits.put_nowait(event)

        async with running(bot, fake):
            presence: dict[str, Any] = {
                "since": None,
                "activities": [],
                "status": "dnd",
                "afk": False,
            }
            bot.gateway.send(op=3, d=presence)
            assert await sent_op(fake, 3) == presence

            await fake.dispatch("RATE_LIMITED", payloads.fixture("events/RATE_LIMITED"))
            await limits.get()

            for _ in range(2):
                await sent_op(fake, 1)
            assert bot.latency is not None

    run(scenario, heartbeat_interval=50)

    (warning,) = [
        record for record in caplog.records if "rate limited" in record.message
    ]
    assert warning.levelno == logging.WARNING
    assert warning.message == "the gateway rate limited opcode 8; retry after 27.5 s"


def test_close_sends_1000_and_cancels_running_listeners(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        @bot.listen
        async def stuck(event: MessageCreate) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.1)
                cancelled.set()

        async with running(bot, fake):
            await fake.dispatch("MESSAGE_CREATE", payloads.message())
            await entered.wait()

        assert cancelled.is_set()
        assert fake.connections[-1].close_code == 1000
        assert bot.latency is None
        await bot.close()

    run(scenario)

    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.skipif(sys.version_info < (3, 14), reason="zstd needs Python 3.14")
def test_a_zstd_compressed_gateway_delivers_events() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        messages: asyncio.Queue[Message] = asyncio.Queue()

        @bot.listen
        async def on_message(event: MessageCreate) -> None:
            messages.put_nowait(event.message)

        async with running(bot, fake):
            message = payloads.message()
            await fake.dispatch("MESSAGE_CREATE", message)
            assert (await messages.get()).id == int(message["id"])

    run(scenario, compress=True)


def test_a_failing_dispatch_is_logged_once_and_the_next_event_arrives(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG, logger="lyrecord.bot")

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        messages: asyncio.Queue[Message] = asyncio.Queue()
        apply = bot.cache.apply

        def apply_or_fail(event: Any) -> None:
            if isinstance(event, ChannelUpdate):
                raise RuntimeError("cache bug")
            apply(event)

        monkeypatch.setattr(bot.cache, "apply", apply_or_fail)

        @bot.listen
        async def on_message(event: MessageCreate) -> None:
            messages.put_nowait(event.message)

        async with running(bot, fake):
            for _ in range(2):
                await fake.dispatch("CHANNEL_UPDATE", payloads.channel())
            message = payloads.message()
            await fake.dispatch("MESSAGE_CREATE", message)
            assert (await messages.get()).id == int(message["id"])

    run(scenario)

    failures = [
        record
        for record in caplog.records
        if record.message == "handling CHANNEL_UPDATE failed"
    ]
    assert [(record.levelno, record.exc_info is not None) for record in failures] == [
        (logging.ERROR, True),
        (logging.DEBUG, True),
    ]


def test_a_raising_listener_is_logged_and_the_other_listeners_still_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        received: asyncio.Queue[int] = asyncio.Queue()

        @bot.listen
        async def broken(event: MessageCreate) -> None:
            raise RuntimeError("listener bug")

        @bot.listen
        async def working(event: MessageCreate) -> None:
            received.put_nowait(event.message.id)

        async with running(bot, fake):
            messages = [payloads.message() for _ in range(2)]
            for message in messages:
                await fake.dispatch("MESSAGE_CREATE", message)
            assert [await received.get() for _ in messages] == [
                int(message["id"]) for message in messages
            ]

    run(scenario)

    failures = [
        record for record in caplog.records if "broken raised" in record.message
    ]
    assert len(failures) == 2
    assert all(record.message.endswith("on MESSAGE_CREATE") for record in failures)
    assert all(record.exc_info is not None for record in failures)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM handlers need Unix")
def test_run_closes_cleanly_on_sigterm() -> None:
    loops: list[asyncio.AbstractEventLoop] = []

    def new_loop() -> asyncio.AbstractEventLoop:
        loops.append(asyncio.new_event_loop())
        return loops[-1]

    with fake_in_thread() as fake:
        bot = Bot(api_url=fake.api_url)

        @bot.listen
        async def stop_when_ready(event: Ready) -> None:
            signal.raise_signal(signal.SIGTERM)

        bot.run(fake.token, loop_factory=new_loop)

    assert len(loops) == 1
    assert fake.connections[-1].close_code == 1000


def test_run_works_in_a_worker_thread() -> None:
    with fake_in_thread() as fake:
        bot = Bot(api_url=fake.api_url)

        @bot.listen
        async def stop_when_ready(event: Ready) -> None:
            await bot.close()

        with ThreadPoolExecutor(1) as pool:
            pool.submit(bot.run, fake.token).result(10)

    assert fake.connections[-1].close_code == 1000


def test_a_closed_bot_starts_again_in_the_same_or_a_new_event_loop() -> None:
    with fake_in_thread() as fake:
        bot = Bot(api_url=fake.api_url)

        async def connect_twice() -> list[int]:
            connected: list[int] = []
            async with asyncio.timeout(10):
                for _ in range(2):
                    started = asyncio.create_task(bot.start(fake.token))
                    await bot.wait_until_ready()
                    connected.append(len(fake.connections))
                    await bot.close()
                    await started
            return connected

        assert asyncio.run(connect_twice()) == [1, 2]
        assert asyncio.run(connect_twice()) == [3, 4]
