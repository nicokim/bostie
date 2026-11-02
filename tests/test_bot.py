import asyncio
import contextlib
import logging
import signal
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

import msgspec
import pytest
from fakediscord import FakeDiscord, Recorded, payloads, run, running, settle

from bostie import (
    AutocompleteContext,
    Bot,
    CommandSyncError,
    ComponentContext,
    Context,
    ErrorContext,
    Forbidden,
    HTTPException,
    Intents,
    InvalidToken,
    Member,
    MemberRequestRateLimited,
    Message,
    MissingIntent,
    ModalContext,
    ModalSubmitInteraction,
    Option,
    OptionType,
    Permissions,
    PrivilegedIntentsRequired,
    Range,
    Router,
    StateError,
    Status,
    WebhookMessage,
    activity,
    ui,
)
from bostie._bridge import Bridge, StatusDelivery
from bostie.commands import CommandSettings
from bostie.events import (
    BotEvent,
    ChannelUpdate,
    Event,
    GuildCreate,
    InteractionCreate,
    ListenerError,
    MemberJoin,
    MembersChunk,
    MessageCreate,
    MessageDelete,
    RawEvent,
    Ready,
    Resumed,
    ShardBackpressure,
    ShardDisconnect,
    ShardReady,
    ShardStop,
    Started,
    Starting,
    Stopped,
    Stopping,
    TaskError,
    ThreadMembersUpdate,
    UserUpdate,
)
from bostie.gateway import GatewayClient
from bostie.sync import SyncResult

STRING = OptionType.STRING
INTEGER = OptionType.INTEGER
USER = OptionType.USER
MENTIONS = {"parse": ["users", "roles"]}
EPHEMERAL = 64
MEMBER_ID = "820774541721731073"
GUILD = 1335172792320131073
OUTAGE_GUILD = 1335927767040131073


class LevelUp(BotEvent):
    user_id: int


class Unit(Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"


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


def levels(caplog: pytest.LogCaptureFixture, logger: str) -> list[int]:
    return [record.levelno for record in caplog.records if record.name == logger]


def failures(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [
        (record.levelno, str(record.exc_info[1]))
        for record in caplog.records
        if record.name == "bostie.bot" and record.exc_info is not None
    ]


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
        await ctx.update(content=f"{tag} {up!r}")

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
    async def color_set(ctx: Context, member: Member, code: Range[str, 7, 7]) -> None:
        """Give a member a color role."""
        await ctx.respond(f"{member.mention} is now {code}")

    return bot


def test_autosync_puts_only_changes_and_an_explicit_sync_always_puts() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        route = f"/applications/{fake.application_id}/commands"
        bot = example_bot(
            fake,
            intents=Intents.DEFAULT | Intents.MESSAGE_CONTENT,
            status=Status.IDLE,
            activities=[activity.streaming("dice", "https://twitch.tv/bostie")],
            afk=True,
            idle_since=datetime(2026, 11, 1, tzinfo=UTC),
            large_threshold=250,
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
        ]
        assert bot.application_id == fake.application_id
        assert bot.user is not None
        assert bot.user.id == int(fake.bot_user["id"])
        assert identify["intents"] == Intents.DEFAULT | Intents.MESSAGE_CONTENT
        assert "capabilities" not in identify
        assert identify["large_threshold"] == 250
        assert identify["presence"] == {
            "since": 1_793_491_200_000,
            "activities": [
                {"name": "dice", "type": 1, "url": "https://twitch.tv/bostie"}
            ],
            "status": "idle",
            "afk": True,
        }

        fake.requests.clear()
        again = example_bot(fake)
        async with running(again, fake):
            assert fake.put_count == 1
            assert await again.sync_commands() == [SyncResult(None, (), (), ())]
        requests = [(request.method, request.path) for request in fake.requests]
        assert requests == first[:3] + first[2:]
        assert fake.put_count == 2

    run(scenario)


@pytest.mark.parametrize(
    ("options", "error", "match"),
    [
        ({"cache": False, "max_messages": 10}, ValueError, "needs cache=True"),
        ({"cache": False, "presences": True}, ValueError, "needs cache=True"),
        ({"members": True}, MissingIntent, "GUILD_MEMBERS"),
        ({"presences": True}, MissingIntent, "GUILD_PRESENCES"),
        ({"large_threshold": 49}, ValueError, "50 to 250"),
        ({"large_threshold": 251}, ValueError, "50 to 250"),
    ],
)
def test_cache_and_gateway_settings_are_checked_when_the_bot_is_built(
    options: dict[str, Any], error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        Bot(**options)


@pytest.mark.parametrize(
    ("intents", "event", "warning"),
    [
        (
            Intents.DEFAULT,
            MemberJoin,
            (
                "on_event listens to GUILD_MEMBER_ADD, which needs the GUILD_MEMBERS "
                "intent; add it to Bot(intents=...) and turn it on in the Developer "
                "Portal"
            ),
        ),
        (
            Intents.GUILD_MESSAGES,
            GuildCreate,
            (
                "on_event listens to GUILD_CREATE, which needs the GUILDS intent; add "
                "it to Bot(intents=...)"
            ),
        ),
        (Intents.DEFAULT, ThreadMembersUpdate, None),
    ],
)
def test_a_listener_whose_events_no_intent_delivers_logs_a_warning(
    intents: Intents,
    event: type[Event],
    warning: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot = Bot(intents=intents)
    plugin = Router()

    @plugin.listen(event)
    async def on_event(event: Event) -> None:
        pass

    bot.include(plugin)

    assert [
        record.getMessage().partition("<locals>.")[2] for record in caplog.records
    ] == ([] if warning is None else [warning])


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
            payloads.option("code", STRING, "#ff8800"),
        )
        data = payloads.command(
            "role", payloads.group("color", set_color), resolved=payloads.resolved()
        )
        async with running(example_bot(fake), fake):
            callback = await fake.interaction(2, data)

        assert callback.json["data"]["content"] == f"<@{MEMBER_ID}> is now #ff8800"

    run(scenario)


@pytest.mark.parametrize(
    ("bot_delay", "settings", "first"),
    [
        (0, {}, 5),
        (0, {"auto_defer": None}, 4),
        (None, {"auto_defer": 0}, 5),
        (60, {"auto_defer": 0}, 5),
        (60, {}, 4),
    ],
)
def test_a_commands_auto_defer_overrides_the_bots(
    bot_delay: float | None, settings: CommandSettings, first: int
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, auto_defer=bot_delay)

        @bot.command(**settings)
        async def slow(ctx: Context) -> None:
            """Answer after a moment."""
            await settle()
            await ctx.respond("done")

        async with running(bot, fake):
            answer = await fake.interaction(2, payloads.command("slow"))

        assert answer.json["type"] == first

    run(scenario)


def test_a_handler_that_starts_late_logs_a_warning(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bostie.bot.SLOW_HANDLER_START", 0.01)

    def block_the_loop(event: InteractionCreate) -> bool:
        time.sleep(0.05)
        return True

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.command
        async def ping(ctx: Context) -> None:
            """Ping."""
            await ctx.respond("pong")

        async with running(bot, fake):
            await asyncio.gather(
                bot.wait_for(InteractionCreate, check=block_the_loop),
                fake.interaction(2, payloads.command("ping")),
            )

    run(scenario)

    assert levels(caplog, "bostie.bot") == [logging.WARNING]


def test_only_slash_interactions_run_commands_and_unanswered_are_logged(
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

    warnings = [record for record in caplog.records if record.name == "bostie.bot"]
    assert [record.levelno for record in warnings] == [logging.WARNING] * 2
    assert ".silent " in warnings[0].getMessage()
    assert ".thinking " in warnings[1].getMessage()


def test_buttons_are_routed_by_custom_id_template() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = example_bot(fake)
        async with running(bot, fake):
            clicked = await fake.interaction(3, payloads.button("vote:rules:1"))

        assert clicked.json == {
            "type": 7,
            "data": {"content": "rules 1", "allowed_mentions": MENTIONS},
        }

    run(scenario)


def test_context_menu_commands_sync_and_route_by_type_and_name() -> None:
    message = payloads.message(content="hello")
    on_message = payloads.context_menu(
        3, "Inspect", message["id"], {"messages": {message["id"]: message}}
    )
    on_user = payloads.context_menu(2, "Inspect", MEMBER_ID, payloads.resolved())

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.user_command("Inspect")
        async def inspect_member(ctx: Context, member: Member) -> None:
            await ctx.respond(f"member {member.mention}")

        @bot.message_command("Inspect", ephemeral=True)
        async def inspect_message(ctx: Context, message: Message) -> None:
            await ctx.respond(f"message {message.content}")

        async with running(bot, fake):
            user_answer = await fake.interaction(2, on_user)
            message_answer = await fake.interaction(2, on_message)

        (put,) = [request for request in fake.requests if request.method == "PUT"]
        assert [(command["type"], command["name"]) for command in put.json] == [
            (2, "Inspect"),
            (3, "Inspect"),
        ]
        assert user_answer.json["data"]["content"] == f"member <@{MEMBER_ID}>"
        assert message_answer.json["data"] == {
            "content": "message hello",
            "allowed_mentions": MENTIONS,
            "flags": EPHEMERAL,
        }

    run(scenario)


def test_modal_submits_are_routed_by_template_and_all_reach_listeners() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        seen: asyncio.Queue[str] = asyncio.Queue()

        @bot.on_modal("feedback:{ticket:int}")
        async def feedback(ctx: ModalContext, ticket: int) -> None:
            await ctx.respond(f"#{ticket}: {ctx.text('feedback')}", ephemeral=True)

        @bot.on_modal("broken")
        async def broken(ctx: ModalContext) -> None:
            raise RuntimeError("boom")

        @bot.on_error
        async def explain(ctx: ErrorContext, error: Exception) -> None:
            assert isinstance(ctx, ModalContext)
            await ctx.respond(f"{ctx.custom_id} failed: {error}")

        @bot.listen
        async def on_interaction(event: InteractionCreate) -> None:
            if isinstance(event.interaction, ModalSubmitInteraction):
                seen.put_nowait(event.interaction.data.custom_id)

        async with running(bot, fake):
            routed = await fake.interaction(5, payloads.modal("feedback:42"))
            failed = await fake.interaction(5, payloads.modal("broken"))
            unrouted = payloads.interaction(5, payloads.modal("survey"))
            await fake.dispatch("INTERACTION_CREATE", unrouted)
            assert [await seen.get() for _ in range(3)] == [
                "feedback:42",
                "broken",
                "survey",
            ]
            await settle()

        assert routed.json["data"] == {
            "content": "#42: The new queue command is great",
            "allowed_mentions": MENTIONS,
            "flags": EPHEMERAL,
        }
        assert failed.json["data"]["content"] == "broken failed: boom"
        assert not any(unrouted["id"] in request.path for request in fake.requests)

    run(scenario)


@pytest.mark.parametrize("modal", [False, True])
def test_a_bot_with_only_component_or_modal_routes_answers_them(modal: bool) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        async def go(ctx: ComponentContext | ModalContext) -> None:
            await ctx.respond("went")

        (bot.on_modal if modal else bot.on_component)("go")(go)
        async with running(bot, fake):
            answer = await fake.interaction(
                5 if modal else 3,
                payloads.modal("go") if modal else payloads.button("go"),
            )

        assert answer.json["type"] == 4

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
        messages: asyncio.Queue[Message | WebhookMessage] = asyncio.Queue()

        @bot.listen
        async def on_message(event: MessageCreate) -> None:
            messages.put_nowait(event.message)

        async with running(bot, fake):
            guild = payloads.guild()
            created, _ = await asyncio.gather(
                bot.wait_for(GuildCreate), fake.dispatch("GUILD_CREATE", guild)
            )
            cached_guild = bot.cache.guild(int(guild["id"]))
            assert cached_guild is not None
            assert cached_guild.name == guild["name"]
            assert created.guild.id == cached_guild.id

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


def test_listeners_see_the_cache_already_updated() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        seen: asyncio.Queue[bool] = asyncio.Queue()

        @bot.listen
        async def on_guild(event: GuildCreate) -> None:
            seen.put_nowait(bot.cache.guild(event.guild.id) is not None)

        async with running(bot, fake):
            await fake.dispatch("GUILD_CREATE", payloads.guild())
            assert await seen.get()

    run(scenario)


def test_the_dispatch_filter_follows_listeners_waiters_and_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filters: list[list[str] | None] = []
    send_filter = GatewayClient._filter

    def record(gateway: GatewayClient, names: Iterable[str] | None) -> None:
        filters.append(None if names is None else list(names))
        send_filter(gateway, names)

    monkeypatch.setattr(GatewayClient, "_filter", record)

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, cache=False)
        plugin = Router()
        bot.include(plugin)
        deleted: asyncio.Queue[int] = asyncio.Queue()
        guilds = ["GUILD_CREATE", "GUILD_DELETE"]
        async with running(bot, fake):
            assert filters[-1] == [*guilds, "RATE_LIMITED"]
            updated, _ = await asyncio.gather(
                bot.wait_for(ChannelUpdate),
                fake.dispatch("CHANNEL_UPDATE", payloads.channel()),
            )
            assert bot.cache.channel(updated.channel.id) is None
            assert filters[-2:] == [
                ["CHANNEL_UPDATE", *guilds, "RATE_LIMITED"],
                [*guilds, "RATE_LIMITED"],
            ]

            @plugin.listen
            async def on_delete(event: MessageDelete) -> None:
                deleted.put_nowait(event.id)

            assert filters[-1] == [*guilds, "MESSAGE_DELETE", "RATE_LIMITED"]
            await fake.dispatch("MESSAGE_DELETE", {"id": "3", "channel_id": "1"})
            assert await deleted.get() == 3

            plugin.remove_listener(on_delete)
            assert filters[-1] == [*guilds, "RATE_LIMITED"]
            assert (plugin.listeners, MessageDelete.NAME in bot.listeners) == (
                {},
                False,
            )

    run(scenario)


def test_dispatched_events_reach_listeners_and_waiters_but_not_the_filter() -> None:
    async def main() -> None:
        bot = Bot()
        heard: asyncio.Queue[LevelUp] = asyncio.Queue()

        @bot.listen
        async def on_level_up(event: LevelUp) -> None:
            heard.put_nowait(event)

        async with bot.stream(LevelUp) as levels:
            bot.dispatch(LevelUp(user_id=7))
            streamed = await anext(levels)
            names = bot._dispatch_names()

        assert streamed == await heard.get() == LevelUp(user_id=7)
        assert names is not None
        assert (LevelUp.NAME, LevelUp.NAME in names) == (f"{__name__}.LevelUp", False)

    asyncio.run(main())


def test_startup_waits_for_the_ready_guilds_and_sends_started_once() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        fake.ready_guilds = [GUILD, OUTAGE_GUILD]
        bot = Bot(api_url=fake.api_url, guild_ready_timeout=0.05)
        seen: list[tuple[object, ...]] = []

        @bot.listen(GuildCreate, ShardReady, Started, ShardDisconnect)
        async def follow(event: Event | ShardReady | Started | ShardDisconnect) -> None:
            match event:
                case GuildCreate():
                    seen.append((event.guild.id, event.joined))
                case ShardDisconnect():
                    seen.append((event.code, event.resuming))
                case _:
                    seen.append((event.NAME,))

        started = asyncio.create_task(bot.start(fake.token))
        ready = asyncio.create_task(bot.wait_until_ready())
        await fake.next_identify()
        await fake.dispatch("GUILD_CREATE", payloads.guild())
        waiting = (bot.is_ready, ready.done())
        await fake.dispatch(
            "GUILD_DELETE", {"id": str(OUTAGE_GUILD), "unavailable": True}
        )
        await ready
        assert (waiting, bot.is_ready) == ((False, False), True)

        await fake.close_gateway(4007)
        await fake.next_identify()
        renewed = asyncio.ensure_future(bot.wait_for(ShardReady))
        await fake.dispatch("GUILD_CREATE", payloads.guild())
        await renewed
        joined = payloads.snowflake()
        await asyncio.gather(
            bot.wait_for(GuildCreate),
            fake.dispatch("GUILD_CREATE", payloads.guild(id=joined)),
        )
        await bot.close()
        await started

        assert seen == [
            (GUILD, False),
            ("shard.ready",),
            ("bot.started",),
            (4007, False),
            (GUILD, False),
            ("shard.ready",),
            (int(joined), True),
        ]
        assert not bot.is_ready

    run(scenario)


@pytest.mark.parametrize(
    ("intents", "large", "chunked"),
    [
        (Intents.GUILD_MEMBERS, False, True),
        (Intents.GUILD_MEMBERS | Intents.GUILD_PRESENCES, True, True),
        (Intents.GUILD_MEMBERS | Intents.GUILD_PRESENCES, False, False),
    ],
)
def test_a_member_cache_asks_for_missing_members_before_the_shard_is_ready(
    intents: Intents, large: bool, chunked: bool
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        fake.ready_guilds = [GUILD]
        presences = Intents.GUILD_PRESENCES in intents
        bot = Bot(
            api_url=fake.api_url,
            intents=Intents.DEFAULT | intents,
            members=True,
            presences=presences,
        )
        started = asyncio.create_task(bot.start(fake.token))
        await fake.next_identify()
        await fake.dispatch("GUILD_CREATE", payloads.guild(large=large))
        if chunked:
            request = await sent_op(fake, 8)
            waiting = bot.is_ready
            chunk = payloads.fixture("events/GUILD_MEMBERS_CHUNK")
            members = [payloads.member(user=payloads.user(id=str(GUILD + 1)))]
            await fake.dispatch(
                "GUILD_MEMBERS_CHUNK",
                chunk | {"members": members, "nonce": request["nonce"]},
            )
            assert not waiting
            assert (request["query"], request["limit"]) == ("", 0)
            assert request["presences"] is presences
        await bot.wait_until_ready()
        cached = bot.cache.member(GUILD, GUILD + 1)
        await bot.close()
        await started

        assert (cached is not None) is chunked
        assert len(bot.cache.guild_members(GUILD)) == 4 + chunked

    run(scenario)


def test_a_session_file_resumes_the_sessions_of_the_last_run(
    tmp_path: Path,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        path = tmp_path / "sessions.json"
        first = Bot(api_url=fake.api_url, session_file=path)
        async with running(first, fake):
            await fake.next_identify()
        saved = path.read_bytes()
        await fake.dispatch("MESSAGE_DELETE", {"id": "5", "channel_id": "1"})
        second = Bot(api_url=fake.api_url, session_file=path)
        missed = asyncio.ensure_future(second.wait_for(MessageDelete))
        async with running(second, fake):
            resume = await fake.next_resume()
            assert (await missed).id == 5
            user = second.user
        later = [payload["op"] for payload in fake.connections[1].received]

        assert fake.connections[0].close_code == 4000
        assert msgspec.json.decode(saved)[0]["seq"] == resume["seq"] == 1
        assert path.read_bytes() != saved
        assert user is not None
        assert user.id == int(fake.bot_user["id"])
        assert 2 not in later

    run(scenario)


def test_user_update_replaces_the_bot_user() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            await asyncio.gather(
                bot.wait_for(UserUpdate),
                fake.dispatch("USER_UPDATE", fake.bot_user | {"username": "renamed"}),
            )

            assert bot.user is not None
            assert bot.user.username == "renamed"

    run(scenario)


def test_raw_listeners_receive_events_bostie_does_not_model() -> None:
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
            await fake.dispatch("BRAND_NEW_EVENT_2", {"answer": 43})
            names = [(await everything.get()).name for _ in range(3)]
            assert names == ["READY", "BRAND_NEW_EVENT", "BRAND_NEW_EVENT_2"]

    run(scenario)


@pytest.mark.parametrize(
    ("replayed", "age", "first"),
    [(True, 60.0, None), (True, 2.5, 5), (False, 60.0, 4)],
)
def test_a_replayed_interaction_is_timed_from_its_creation(
    replayed: bool, age: float, first: int | None, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, auto_defer=1.0)
        handled: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        @bot.command
        async def ping(ctx: Context) -> None:
            """Ping."""
            await settle()
            await ctx.respond("pong")
            handled.set_result(None)

        created = payloads.snowflake(time.time() - age)
        interaction = payloads.interaction(2, payloads.command("ping"), id=created)
        async with running(bot, fake):
            resumed = asyncio.Task(
                bot.wait_for(Resumed), loop=asyncio.get_running_loop(), eager_start=True
            )
            await fake.send_op(7)
            if replayed:
                await fake.dispatch("INTERACTION_CREATE", interaction)
            await resumed
            if not replayed:
                await fake.dispatch("INTERACTION_CREATE", interaction)
            if first is not None:
                await handled

        callbacks = [
            r.json["type"] for r in fake.requests if r.path.endswith("/callback")
        ]
        assert callbacks == ([] if first is None else [first])

    run(scenario)

    assert len(levels(caplog, "bostie.bot")) == (1 if first is None else 0)


def test_stopping_before_ready_fails_wait_until_ready_and_close_ends_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        monkeypatch.delenv("BOSTIE_TOKEN", raising=False)
        bot = Bot(api_url=fake.api_url)
        started = asyncio.create_task(bot.start(fake.token))
        waiting = asyncio.create_task(bot.wait_until_ready())
        await bot.close()
        await started
        with pytest.raises(StateError, match="stopped before it was ready"):
            await waiting
        assert not fake.connections

        failures: list[tuple[str | None, type[Exception]]] = [
            (None, ValueError),
            ("wrong-token", InvalidToken),
        ]
        for token, error in failures:
            started = asyncio.create_task(bot.start(token))
            with pytest.raises(StateError, match="stopped before it was ready"):
                await bot.wait_until_ready()
            with pytest.raises(error):
                await started

        async with running(bot, fake):
            await bot.wait_until_ready()

        connections = len(fake.connections)
        started = asyncio.create_task(bot.start(fake.token))
        await bot.close()
        await started
        assert len(fake.connections) == connections

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
        gateway_bot = payloads.fixture("rest/gateway_bot")
        exhausted = gateway_bot["session_start_limit"] | {
            "remaining": 0,
            "reset_after": 300,
        }
        fake.fail(
            "GET",
            "/gateway/bot",
            200,
            gateway_bot | {"url": fake.gateway_url, "session_start_limit": exhausted},
        )
        async with running(Bot(api_url=fake.api_url), fake):
            await fake.next_identify()

    run(scenario)

    assert levels(caplog, "bostie.gateway") == [logging.WARNING]


def test_start_resolves_the_token_and_raises_fatal_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        for client in ("http", "rest"):
            with pytest.raises(StateError, match="started"):
                getattr(bot, client)
        monkeypatch.delenv("BOSTIE_TOKEN", raising=False)
        with pytest.raises(ValueError, match="BOSTIE_TOKEN"):
            await bot.start()

        monkeypatch.setenv("BOSTIE_TOKEN", f"Bot {fake.token}")
        started = asyncio.create_task(bot.start())
        await fake.next_identify()
        await fake.close_gateway(4014)
        with pytest.raises(PrivilegedIntentsRequired):
            await started

        with pytest.raises(InvalidToken):
            await Bot(api_url=fake.api_url).start("wrong-token")
        assert fake.requests[-1].path == "/gateway/bot"

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
        async def pass_up(ctx: ErrorContext, error: Forbidden) -> None:
            hooks.append("admin")
            raise error

        @bot.on_error(HTTPException)
        async def explain(ctx: ErrorContext, error: HTTPException) -> None:
            hooks.append("bot")
            assert isinstance(ctx, Context)
            await ctx.respond(f"Discord said {error.code}", ephemeral=True)

        @admin.command
        async def purge(ctx: Context) -> None:
            """Purge a channel."""
            await bot.rest.channels.fetch(channel_id)

        @bot.command
        async def crash(ctx: Context) -> None:
            """Crash."""
            raise RuntimeError("boom")

        bot.include(admin)
        async with running(bot, fake):
            fake.fail(
                "GET",
                f"/channels/{channel_id}",
                403,
                payloads.fixture("rest/error_403"),
            )
            explained = await fake.interaction(2, payloads.command("purge"))
            crashed = await fake.interaction(2, payloads.command("crash"))

        assert hooks == ["admin", "bot"]
        assert explained.json["data"]["content"] == "Discord said 50013"
        assert crashed.json["data"]["content"] == FAILED["content"]

    run(scenario)

    assert failures(caplog) == [(logging.ERROR, "boom")]


FAILED = {"content": "Something went wrong.", "allowed_mentions": MENTIONS}
FAILED_EPHEMERALLY = FAILED | {"flags": EPHEMERAL}


def answered_with(request: Recorded) -> tuple[str, str, Any]:
    target = request.path.rsplit("/", 1)[-1]
    if target not in ("callback", "@original"):
        target = "followup"
    return request.method, target, request.json


BEFORE_RAISING: dict[str, Callable[[Any], Awaitable[object]]] = {
    "pending": lambda ctx: asyncio.sleep(0),
    "deferred": lambda ctx: ctx.defer(),
    "deferred_update": lambda ctx: ctx.defer_update(),
    "responded": lambda ctx: ctx.respond("first"),
}


@pytest.mark.parametrize(
    ("kind", "before", "answer"),
    [
        (
            Context,
            "pending",
            ("POST", "callback", {"type": 4, "data": FAILED_EPHEMERALLY}),
        ),
        (
            ComponentContext,
            "pending",
            ("POST", "callback", {"type": 4, "data": FAILED_EPHEMERALLY}),
        ),
        (Context, "deferred", ("PATCH", "@original", FAILED)),
        (ComponentContext, "deferred", ("PATCH", "@original", FAILED)),
        (ComponentContext, "deferred_update", ("POST", "followup", FAILED_EPHEMERALLY)),
        (Context, "responded", None),
        (ComponentContext, "responded", None),
    ],
)
def test_a_handler_that_raises_gets_the_answer_its_state_allows(
    kind: type[Context | ComponentContext],
    before: str,
    answer: tuple[str, str, dict[str, Any]] | None,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, auto_defer=None)
        raised: asyncio.Future[tuple[Context | ComponentContext, int]] = (
            asyncio.get_running_loop().create_future()
        )

        async def fail(ctx: Context | ComponentContext) -> None:
            await BEFORE_RAISING[before](ctx)
            raised.set_result((ctx, len(fake.requests)))
            raise RuntimeError("boom")

        if kind is Context:

            @bot.command
            async def command(ctx: Context) -> None:
                """Fail."""
                await fail(ctx)

            payload = payloads.interaction(2, payloads.command("command"))
        else:

            @bot.on_component("component")
            async def component(ctx: ComponentContext) -> None:
                await fail(ctx)

            payload = payloads.interaction(3, payloads.button("component"))
        async with running(bot, fake):
            await fake.dispatch("INTERACTION_CREATE", payload)
            ctx, start = await raised
            await ctx.followup("sent once the failure answer is done")

        sent = [answered_with(request) for request in fake.requests[start:-1]]
        assert sent == ([] if answer is None else [answer])

    run(scenario)


def test_a_raising_autocomplete_answers_with_no_choices() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        async def broken(ctx: AutocompleteContext, value: str) -> list[str]:
            raise RuntimeError("boom")

        @bot.command
        async def lookup(
            ctx: Context, query: Annotated[str, Option(autocomplete=broken)]
        ) -> None:
            """Look something up."""

        focused = payloads.option("query", STRING, "x", focused=True)
        async with running(bot, fake):
            suggested = await fake.interaction(4, payloads.command("lookup", focused))

        assert suggested.json == {"type": 8, "data": {"choices": []}}

    run(scenario)


def test_gateway_rate_limits_and_latency_reach_the_bot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        limits: asyncio.Queue[RawEvent] = asyncio.Queue()

        @bot.listen_raw("RATE_LIMITED")
        async def on_rate_limit(event: RawEvent) -> None:
            limits.put_nowait(event)

        async with running(bot, fake):
            await fake.dispatch("RATE_LIMITED", payloads.fixture("events/RATE_LIMITED"))
            await limits.get()

            for _ in range(2):
                await sent_op(fake, 1)
            assert bot.latency is not None

    run(scenario, heartbeat_interval=50)

    assert levels(caplog, "bostie.bot") == [logging.WARNING]


@pytest.mark.parametrize(
    ("delivery", "event"),
    [
        (
            ("stopped", 1, 4014, "disallowed intents", None),
            ShardStop(shard_id=1, code=4014, reason="disallowed intents"),
        ),
        (
            ("backpressure", 1, 70_000_000),
            ShardBackpressure(shard_id=1, queued_bytes=70_000_000),
        ),
    ],
)
def test_shard_stops_and_backpressure_reach_listeners(
    delivery: StatusDelivery, event: ShardStop | ShardBackpressure
) -> None:
    async def main() -> None:
        bot = Bot()
        bridge = Bridge("token", "http://127.0.0.1:1/api/v10", lambda *_: None)
        bot._gateway = GatewayClient(
            bridge, "token", intents=0, shard_count=2, shard_ids=[1]
        )
        async with bot.stream(type(event)) as events:
            bot._on_gateway(delivery, 0.0)
            assert await anext(events) == event
        await bridge.close()

    asyncio.run(main())


def test_request_members_gathers_every_chunk_with_its_nonce() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(
            api_url=fake.api_url, intents=Intents.DEFAULT | Intents.GUILD_PRESENCES
        )
        heard: list[int] = []

        @bot.listen
        async def on_chunk(event: MembersChunk) -> None:
            heard.append(event.chunk_index)

        async with running(bot, fake):
            requested = asyncio.ensure_future(
                bot.request_members(1, user_ids=[2, 3], presences=True)
            )
            request = await sent_op(fake, 8)
            chunk = payloads.fixture("events/GUILD_MEMBERS_CHUNK") | {"chunk_count": 2}
            for index, nonce in [
                (0, "other"),
                (0, request["nonce"]),
                (1, request["nonce"]),
            ]:
                await fake.dispatch(
                    "GUILD_MEMBERS_CHUNK",
                    chunk | {"chunk_index": index, "nonce": nonce},
                )
            members = await requested

        assert request == {
            "guild_id": "1",
            "presences": True,
            "nonce": request["nonce"],
            "user_ids": ["2", "3"],
        }
        assert (len(members.members), len(members.presences)) == (4, 4)
        assert members.not_found == (1_562_134_550_937_731_073,) * 2
        assert heard == [0, 0, 1]

    run(scenario)


@pytest.mark.parametrize(
    ("options", "error", "match"),
    [
        ({}, MissingIntent, "GUILD_MEMBERS"),
        ({"query": "ad", "limit": 5, "presences": True}, MissingIntent, "PRESENCES"),
        ({"user_ids": [2], "limit": 5}, ValueError, "not both"),
        ({"query": "ad", "limit": 101}, ValueError, "0 to 100"),
        ({"user_ids": range(101)}, ValueError, "at most 100"),
    ],
)
def test_request_members_checks_intents_and_options_before_sending(
    options: dict[str, Any], error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        asyncio.run(Bot().request_members(1, **options))


def test_a_rate_limited_member_request_raises_with_its_retry_after(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, intents=Intents.DEFAULT | Intents.GUILD_MEMBERS)
        async with running(bot, fake):
            requested = asyncio.ensure_future(bot.request_members(1))
            request = await sent_op(fake, 8)
            limit = payloads.fixture("events/RATE_LIMITED")
            limit["meta"]["nonce"] = request["nonce"]
            await fake.dispatch("RATE_LIMITED", limit)
            with pytest.raises(MemberRequestRateLimited) as raised:
                await requested

        assert raised.value.retry_after == limit["retry_after"]
        assert (request["query"], request["limit"]) == ("", 0)

    run(scenario)

    assert levels(caplog, "bostie.bot") == []


def test_a_member_request_whose_chunks_stop_raises_timeout_error() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            requested = asyncio.ensure_future(
                bot.request_members(1, query="ad", limit=5, timeout=0.1)
            )
            request = await sent_op(fake, 8)
            chunk = payloads.fixture("events/GUILD_MEMBERS_CHUNK")
            await fake.dispatch(
                "GUILD_MEMBERS_CHUNK",
                chunk | {"chunk_count": 2, "nonce": request["nonce"]},
            )
            with pytest.raises(TimeoutError):
                await requested

    run(scenario)


def test_member_requests_fail_at_once_while_their_shard_renews_its_session() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            release = fake.hold_identify(0)
            requested = asyncio.ensure_future(
                bot.request_members(GUILD, query="ad", limit=5)
            )
            await sent_op(fake, 8)
            disconnected = asyncio.ensure_future(bot.wait_for(ShardDisconnect))
            await fake.close_gateway(4007)
            assert (await disconnected).resuming is False

            with pytest.raises(StateError, match="dropped the request"):
                await requested
            with pytest.raises(StateError, match="starting a new session"):
                await bot.request_members(GUILD, query="ad", limit=5)
            release.set()

    run(scenario)


def test_soundboard_and_channel_info_requests_return_their_guilds_answer() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        sounds = payloads.fixture("events/SOUNDBOARD_SOUNDS")
        info = payloads.fixture("events/CHANNEL_INFO")
        async with running(bot, fake):
            requested_sounds = asyncio.ensure_future(
                bot.request_soundboard_sounds(GUILD)
            )
            sounds_request = await sent_op(fake, 31)
            await fake.dispatch("SOUNDBOARD_SOUNDS", sounds | {"guild_id": "1"})
            await fake.dispatch("SOUNDBOARD_SOUNDS", sounds)
            requested_info = asyncio.ensure_future(
                bot.request_channel_info(GUILD, fields=["status"])
            )
            info_request = await sent_op(fake, 43)
            await fake.dispatch("CHANNEL_INFO", info)

            assert sounds_request == {"guild_ids": [str(GUILD)]}
            assert info_request == {"guild_id": str(GUILD), "fields": ["status"]}
            assert [sound.name for sound in await requested_sounds] == [
                sound["name"] for sound in sounds["soundboard_sounds"]
            ]
            assert [channel.id for channel in await requested_info] == [
                int(channel["id"]) for channel in info["channels"]
            ]

    run(scenario)


def test_stream_queues_matching_events_until_its_block_ends() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            async with bot.stream(MessageDelete, check=lambda e: e.id != 2) as deletes:
                for id in ("1", "2", "3"):
                    await fake.dispatch("MESSAGE_DELETE", {"id": id, "channel_id": "1"})
                received = [(await anext(deletes)).id for _ in range(2)]

            assert received == [1, 3]

    run(scenario)


def test_listener_errors_go_to_listener_error_listeners_instead_of_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        reported: asyncio.Queue[ListenerError] = asyncio.Queue()

        @bot.listen
        async def broken(event: MessageCreate) -> None:
            raise RuntimeError("listener bug")

        @bot.listen
        async def report(failure: ListenerError) -> None:
            reported.put_nowait(failure)

        async with running(bot, fake):
            await fake.dispatch("MESSAGE_CREATE", payloads.message())
            failure = await reported.get()

        assert failure.listener is broken
        assert isinstance(failure.event, MessageCreate)
        assert str(failure.error) == "listener bug"

    run(scenario)

    assert failures(caplog) == []


def test_stopping_listeners_finish_while_the_gateway_is_still_open() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        open_codes: list[int | None] = []

        @bot.listen
        async def goodbye(event: Stopping) -> None:
            await asyncio.sleep(0.05)
            open_codes.append(fake.connections[-1].close_code)

        async with running(bot, fake):
            pass

        assert open_codes == [None]
        assert fake.connections[-1].close_code == 1000

    run(scenario)


def test_starting_comes_before_sync_and_connect_and_stopped_after_close() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        late = Router()
        seen: list[tuple[str, object]] = []

        @late.command
        async def ping(ctx: Context) -> None:
            """Answer pong."""

        @bot.listen
        async def set_up(event: Starting) -> None:
            me = await bot.rest.users.fetch_current()
            seen.append(("starting", (me.username, len(fake.connections))))
            bot.include(late)

        @bot.listen
        async def tear_down(event: Stopped) -> None:
            seen.append(("stopped", fake.connections[-1].close_code))

        async with running(bot, fake):
            assert [command["name"] for command in fake.commands] == ["ping"]

        assert seen == [
            ("starting", (fake.bot_user["username"], 0)),
            ("stopped", 1000),
        ]

    run(scenario)


def test_a_raising_starting_listener_stops_start_before_it_connects() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        stopped: list[Stopped] = []

        @bot.listen
        async def set_up(event: Starting) -> None:
            raise RuntimeError("no database")

        @bot.listen
        async def tear_down(event: Stopped) -> None:
            stopped.append(event)

        with pytest.raises(RuntimeError, match="no database"):
            await bot.start(fake.token)

        assert fake.connections == []
        assert len(stopped) == 1

    run(scenario)


def test_tasks_run_while_ready_report_errors_and_stop_on_close(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        ready: list[bool] = []
        reported: asyncio.Queue[TaskError] = asyncio.Queue()
        stuck = asyncio.Event()
        cancelled = asyncio.Event()

        @bot.every(seconds=0.01)
        async def tick() -> None:
            ready.append(bot.is_ready)
            if len(ready) == 1:
                raise RuntimeError("first run")
            if len(ready) == 3:
                stuck.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        @bot.listen
        async def report(failure: TaskError) -> None:
            reported.put_nowait(failure)

        async with running(bot, fake):
            failure = await reported.get()
            await stuck.wait()

        assert cancelled.is_set()
        assert ready == [True, True, True]
        assert failure.task is tick
        assert str(failure.error) == "first run"

    run(scenario)

    assert failures(caplog) == []


def test_a_router_included_while_running_runs_its_tasks_until_excluded() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        jobs = Router()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        @jobs.every(seconds=60)
        async def wait() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with running(bot, fake):
            bot.include(jobs)
            await started.wait()
            bot.exclude(jobs)
            await cancelled.wait()

    run(scenario)


def test_two_bots_run_in_one_process_and_close_on_their_own() -> None:
    def named_bot(fake: FakeDiscord, name: str) -> Bot:
        bot = Bot(api_url=fake.api_url)

        @bot.command
        async def who(ctx: Context) -> None:
            """Say which bot answers."""
            await ctx.respond(name)

        return bot

    async def scenario(fake: FakeDiscord) -> None:
        async with FakeDiscord() as other:
            first, second = named_bot(fake, "first"), named_bot(other, "second")
            async with running(second, other):
                async with running(first, fake):
                    replies = await asyncio.gather(
                        fake.interaction(2, payloads.command("who")),
                        other.interaction(2, payloads.command("who")),
                    )
                after_close = await other.interaction(2, payloads.command("who"))

        assert [reply.json["data"]["content"] for reply in replies] == [
            "first",
            "second",
        ]
        assert after_close.json["data"]["content"] == "second"

    run(scenario)


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
        messages: asyncio.Queue[Message | WebhookMessage] = asyncio.Queue()

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
    caplog.set_level(logging.DEBUG, logger="bostie.bot")

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        messages: asyncio.Queue[Message | WebhookMessage] = asyncio.Queue()
        apply = bot.cache.apply

        def apply_or_fail(event: Any) -> None:
            if event.NAME == ChannelUpdate.NAME:
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

    assert failures(caplog) == [
        (logging.ERROR, "cache bug"),
        (logging.DEBUG, "cache bug"),
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

    assert failures(caplog) == [(logging.ERROR, "listener bug")] * 2


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
