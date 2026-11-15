import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from bostie import Bot, voice
from bostie.enums import Intents, SpeakingFlags
from bostie.errors import Forbidden, MissingIntent, NotFound, StateError
from bostie.events import GuildCreate, VoiceServerUpdate, VoiceStateUpdate
from bostie.testing import FakeDiscord, payloads, run, running
from bostie.testing.payloads import Json
from bostie.voice.formats import StageRole

GUILD_ID = int(payloads.voice_channel()["guild_id"])
VOICE_ID = int(payloads.voice_channel()["id"])
STAGE_ID = int(payloads.stage_channel()["id"])
OTHER_ID = VOICE_ID + 1
USER_ID = int(payloads.member()["user"]["id"])
PATCH_ME = f"/guilds/{GUILD_ID}/voice-states/@me"
SILENCE = bytes(3840)


@contextlib.asynccontextmanager
async def started(fake: FakeDiscord, **options: Any) -> AsyncIterator[Bot]:
    bot = Bot(api_url=fake.api_url, **options)
    async with running(bot, fake):
        yield bot


def joined(
    channel_id: int | None, *, self_mute: bool = False, self_deaf: bool = False
) -> Json:
    return {
        "guild_id": str(GUILD_ID),
        "channel_id": None if channel_id is None else str(channel_id),
        "self_mute": self_mute,
        "self_deaf": self_deaf,
    }


def sent(fake: FakeDiscord, op: int) -> list[Any]:
    payloads = []
    while not fake.gateway_received.empty():
        payload = fake.gateway_received.get_nowait()
        if payload["op"] == op:
            payloads.append(payload["d"])
    return payloads


def voice_ops(fake: FakeDiscord, op: int) -> list[Any]:
    found = []
    while not fake.voice.received.empty():
        payload = fake.voice.received.get_nowait()
        if isinstance(payload, dict) and payload["op"] == op:
            found.append(payload["d"])
    return found


def patches(fake: FakeDiscord, since: int = 0) -> list[Any]:
    return [
        request.json
        for request in fake.requests[since:]
        if (request.method, request.path) == ("PATCH", PATCH_ME)
    ]


def lookups(fake: FakeDiscord) -> list[str]:
    return [
        request.path
        for request in fake.requests
        if request.path.startswith("/channels/") or "/voice-states/" in request.path
    ]


def add_voice_channel(fake: FakeDiscord, channel_id: int) -> None:
    fake.channels[str(channel_id)] = payloads.voice_channel(id=str(channel_id))


def test_connect_applies_its_arguments_over_the_bot_defaults() -> None:
    defaults = voice.Defaults(self_deaf=True, self_mute=True, stall_after=5.0)

    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake, voice=defaults) as bot:
            vc = await bot.voice.connect(
                VOICE_ID,
                self_mute=False,
                speaking_flags=SpeakingFlags.SOUNDSHARE,
                speech_activity=False,
                stall_after=0.5,
            )
            vc.sink().write_nowait(SILENCE)
            await fake.voice.wait_for_packets(1)

            assert (vc.speaking_flags, vc.speech_activity, vc.stall_after) == (
                SpeakingFlags.SOUNDSHARE,
                False,
                0.5,
            )
            assert voice_ops(fake, 5)[0]["speaking"] == SpeakingFlags.SOUNDSHARE
        assert sent(fake, 4)[0] == joined(VOICE_ID, self_deaf=True)

    run(scenario, voice=True)


def test_an_invalid_connect_setting_raises_before_anything_is_sent() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            with pytest.raises(ValueError, match="timeout"):
                await bot.voice.connect(VOICE_ID, timeout=0.0)
        assert (sent(fake, 4), lookups(fake)) == ([], [])

    run(scenario, voice=True)


@pytest.mark.parametrize("call", ["connect", "connect_external"])
def test_voice_needs_the_guild_voice_states_intent(call: str) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake, intents=Intents.GUILDS) as bot:
            attempt = (
                bot.voice.connect(VOICE_ID)
                if call == "connect"
                else bot.voice.connect_external(GUILD_ID, VOICE_ID)
            )
            with pytest.raises(MissingIntent, match=r"Intents\.GUILD_VOICE_STATES"):
                await attempt
        assert sent(fake, 4) == []

    run(scenario, voice=True)


def test_voice_needs_a_running_bot() -> None:
    async def scenario() -> None:
        with pytest.raises(StateError, match=r"bot\.voice\.connect\(\) needs a run"):
            await Bot().voice.connect(VOICE_ID)

    asyncio.run(scenario())


def test_a_cached_channel_needs_no_request() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            await asyncio.gather(
                bot.wait_for(GuildCreate),
                fake.dispatch("GUILD_CREATE", payloads.guild()),
            )
            await bot.voice.connect(VOICE_ID)
        assert lookups(fake) == []

    run(scenario, voice=True)


@pytest.mark.parametrize(
    "channel", [payloads.channel(), {"id": "999", "type": 2}], ids=["text", "dm"]
)
def test_only_guild_voice_and_stage_channels_connect(channel: Json) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        fake.channels[channel["id"]] = channel
        async with started(fake) as bot:
            with pytest.raises(ValueError, match="is not a voice or stage channel"):
                await bot.voice.connect(int(channel["id"]))
        assert sent(fake, 4) == []

    run(scenario, voice=True)


@pytest.mark.parametrize(
    ("default", "channel", "options", "asked"),
    [
        ("listen", VOICE_ID, {"stage": "speak"}, []),
        ("speak", STAGE_ID, {}, [{"suppress": False}]),
        ("speak", STAGE_ID, {"stage": "listen"}, []),
    ],
)
def test_a_first_connect_asks_for_the_stage_role_on_a_stage_channel(
    default: StageRole,
    channel: int,
    options: dict[str, Any],
    asked: list[Json],
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake, voice=voice.Defaults(stage=default)) as bot:
            await bot.voice.connect(channel, **options)
        assert patches(fake) == [{"channel_id": channel} | role for role in asked]

    run(scenario, voice=True)


@pytest.mark.parametrize(
    ("default", "start", "entry", "options", "asked"),
    [
        ("listen", STAGE_ID, "connect", {"stage": "speak"}, [{"suppress": False}]),
        ("speak", STAGE_ID, "connect", {"stage": "listen"}, [{"suppress": True}]),
        ("speak", VOICE_ID, "connect", {}, [{"suppress": False}]),
        ("speak", VOICE_ID, "move", {}, [{"suppress": False}]),
        ("listen", VOICE_ID, "move", {}, []),
    ],
)
def test_entering_a_stage_asks_for_the_connections_stage_role(
    default: StageRole,
    start: int,
    entry: str,
    options: dict[str, Any],
    asked: list[Json],
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake, voice=voice.Defaults(stage=default)) as bot:
            vc = await bot.voice.connect(start)
            before = len(fake.requests)
            if entry == "connect":
                await bot.voice.connect(STAGE_ID, **options)
            else:
                await vc.move(STAGE_ID)
            entered = patches(fake, before)

        assert entered == [{"channel_id": STAGE_ID} | role for role in asked]

    run(scenario, voice=True)


def test_a_stage_request_patches_the_time_it_asked_to_speak() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            before = datetime.now(UTC)
            await bot.voice.connect(STAGE_ID, stage="request")
            after = datetime.now(UTC)
        [asked] = patches(fake)
        assert asked.keys() == {"channel_id", "request_to_speak_timestamp"}
        assert before <= datetime.fromisoformat(asked["request_to_speak_timestamp"])
        assert datetime.fromisoformat(asked["request_to_speak_timestamp"]) <= after

    run(scenario, voice=True)


def test_a_failing_stage_request_disconnects_and_raises() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            fake.fail("PATCH", PATCH_ME, 403, {"code": 50013, "message": "Missing"})
            with pytest.raises(Forbidden):
                await bot.voice.connect(STAGE_ID, stage="speak")
            assert bot.voice.get(GUILD_ID) is None
        assert sent(fake, 4) == [joined(STAGE_ID), joined(None)]

    run(scenario, voice=True)


def test_a_second_connect_moves_the_connection_and_waits_for_ready() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            assert await bot.voice.connect(OTHER_ID) is vc
            assert (vc.channel_id, vc.state) == (OTHER_ID, voice.State.READY)
            assert bot.voice.connections == (vc,)
        assert sent(fake, 4) == [joined(VOICE_ID), joined(OTHER_ID), joined(None)]

    run(scenario, voice=True)


def test_a_second_connect_keeps_the_connection_and_applies_only_its_options() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        encoder = voice.EncoderSettings(application="voip")
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID, self_mute=True)
            assert await bot.voice.connect(VOICE_ID) is vc
            again = await bot.voice.connect(
                VOICE_ID,
                self_deaf=True,
                encoder=encoder,
                speaking_flags=SpeakingFlags.SOUNDSHARE,
                speech_hangover=0.5,
                speech_activity=False,
                stall_after=0.5,
            )

            assert again is vc
            assert vc.encoder == encoder
            assert (
                vc.speaking_flags,
                vc.speech_hangover,
                vc.speech_activity,
                vc.stall_after,
            ) == (SpeakingFlags.SOUNDSHARE, 0.5, False, 0.5)
        assert sent(fake, 4) == [
            joined(VOICE_ID, self_mute=True),
            joined(VOICE_ID, self_mute=True, self_deaf=True),
            joined(None),
        ]

    run(scenario, voice=True)


def test_concurrent_connects_both_get_the_ready_connection() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            one, two = await asyncio.gather(
                bot.voice.connect(VOICE_ID), bot.voice.connect(VOICE_ID)
            )
            assert one is two
        assert sent(fake, 4) == [joined(VOICE_ID), joined(None)]

    run(scenario, voice=True)


def test_a_connect_discord_never_answers_raises_for_every_caller() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            release = fake.hold_voice_state()
            results = await asyncio.gather(
                bot.voice.connect(VOICE_ID, timeout=0.05),
                bot.voice.connect(VOICE_ID, timeout=0.05),
                return_exceptions=True,
            )
            release.set()

            for error in results:
                assert isinstance(error, voice.VoiceConnectError)
                assert (error.reason, error.code) == (
                    voice.DisconnectReason.TIMEOUT,
                    None,
                )
                assert error.detail
            assert bot.voice.get(GUILD_ID) is None

    run(scenario, voice=True)


def test_cancelling_a_connect_leaves_the_channel() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            release = fake.hold_voice_state()
            ended = asyncio.create_task(
                bot.wait_for(
                    voice.StateChange,
                    check=lambda event: event.new is voice.State.DISCONNECTED,
                )
            )
            connecting = asyncio.create_task(bot.voice.connect(VOICE_ID))
            asked = await fake.next_voice_state_update()
            connecting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await connecting
            release.set()
            left = await fake.next_voice_state_update()
            await ended

            assert (asked, left) == (joined(VOICE_ID), joined(None))
            assert bot.voice.connections == ()

    run(scenario, voice=True)


def test_connect_external_returns_the_bots_state_and_server_and_leaves() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            release = fake.hold_voice_state()
            connecting = asyncio.create_task(
                bot.voice.connect_external(GUILD_ID, VOICE_ID)
            )
            await fake.next_voice_state_update()
            await fake.dispatch(
                "VOICE_STATE_UPDATE",
                payloads.voice_state(user_id=str(USER_ID), channel_id=str(VOICE_ID)),
            )
            await fake.dispatch(
                "VOICE_SERVER_UPDATE", {"token": "t", "guild_id": "1", "endpoint": None}
            )
            release.set()
            external = await connecting
            state = fake.voice_states[str(GUILD_ID), fake.bot_user["id"]]
            await external.disconnect()
            left = await fake.next_voice_state_update()

            assert external.state.voice_state.session_id == state["session_id"]
            assert external.state.voice_state.user_id == int(fake.bot_user["id"])
            assert external.server.guild_id == GUILD_ID
            assert external.server.token
            assert left == joined(None)
            assert bot.voice.get(GUILD_ID) is None

    run(scenario, voice=True)


def test_connect_external_hands_on_later_servers_and_bot_states() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            external = await bot.voice.connect_external(GUILD_ID, VOICE_ID)
            async with external.updates() as updates:
                await fake.dispatch(
                    "VOICE_STATE_UPDATE",
                    payloads.voice_state(
                        user_id=str(USER_ID), channel_id=str(OTHER_ID)
                    ),
                )
                await fake.dispatch(
                    "VOICE_SERVER_UPDATE",
                    {"token": "x", "guild_id": "1", "endpoint": None},
                )
                await fake.move_bot(OTHER_ID)
                got = [await anext(updates), await anext(updates)]

            states = [u for u in got if isinstance(u, VoiceStateUpdate)]
            servers = [u for u in got if isinstance(u, VoiceServerUpdate)]
            assert [state.voice_state.channel_id for state in states] == [OTHER_ID]
            assert [server.guild_id for server in servers] == [GUILD_ID]

    run(scenario, voice=True)


@pytest.mark.parametrize("ending", ["cancel", "timeout"])
def test_a_connect_external_that_gives_up_leaves_the_channel(ending: str) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            release = fake.hold_voice_state()
            connecting = asyncio.create_task(
                bot.voice.connect_external(GUILD_ID, VOICE_ID, timeout=0.05)
            )
            asked = await fake.next_voice_state_update()
            if ending == "cancel":
                connecting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await connecting
            else:
                with pytest.raises(voice.VoiceConnectError) as raised:
                    await connecting
                assert raised.value.reason is voice.DisconnectReason.TIMEOUT
            release.set()

            assert (asked, await fake.next_voice_state_update()) == (
                joined(VOICE_ID),
                joined(None),
            )

    run(scenario, voice=True)


def test_connect_external_refuses_a_guild_bostie_already_runs() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            await bot.voice.connect(VOICE_ID)
            with pytest.raises(voice.AlreadyConnected):
                await bot.voice.connect_external(GUILD_ID, VOICE_ID)

    run(scenario, voice=True)


def test_context_voice_connects_to_the_users_channel_with_its_options() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        fake.voice_states[str(GUILD_ID), str(USER_ID)] = payloads.voice_state(
            user_id=str(USER_ID), channel_id=str(OTHER_ID)
        )
        async with started(fake) as bot:
            ctx = voice.ContextVoice(bot.voice, GUILD_ID, USER_ID)
            before = ctx.current
            vc = await ctx.connect(self_deaf=True)

            assert before is None
            assert (vc.channel_id, ctx.current) == (OTHER_ID, vc)
            assert voice.ContextVoice(bot.voice, None, USER_ID).current is None
        assert sent(fake, 4)[0] == joined(OTHER_ID, self_deaf=True)

    run(scenario, voice=True)


@pytest.mark.parametrize("cached", [True, False])
def test_context_voice_reads_a_cached_guilds_voice_states(cached: bool) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        state = payloads.voice_state(user_id=str(USER_ID), channel_id=str(VOICE_ID))
        guild = payloads.guild(voice_states=[state] if cached else [])
        async with started(fake) as bot:
            await asyncio.gather(
                bot.wait_for(GuildCreate), fake.dispatch("GUILD_CREATE", guild)
            )
            ctx = voice.ContextVoice(bot.voice, GUILD_ID, USER_ID)
            if cached:
                assert (await ctx.connect()).channel_id == VOICE_ID
            else:
                with pytest.raises(voice.NotInVoice):
                    await ctx.connect()
        assert lookups(fake) == []

    run(scenario, voice=True)


@pytest.mark.parametrize(
    ("case", "error"),
    [("dm", voice.NotInVoice), ("no channel", voice.NotInVoice), ("gone", NotFound)],
)
def test_context_voice_raises_when_the_user_has_no_voice_channel(
    case: str, error: type[Exception]
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        route = f"/guilds/{GUILD_ID}/voice-states/{USER_ID}"
        fake.voice_states[str(GUILD_ID), str(USER_ID)] = payloads.voice_state(
            user_id=str(USER_ID), channel_id=None
        )
        if case == "gone":
            fake.fail("GET", route, 404, {"code": 10004, "message": "Unknown Guild"})
        async with started(fake) as bot:
            guild_id = None if case == "dm" else GUILD_ID
            with pytest.raises(error):
                await voice.ContextVoice(bot.voice, guild_id, USER_ID).connect()
        assert sent(fake, 4) == []

    run(scenario, voice=True)
