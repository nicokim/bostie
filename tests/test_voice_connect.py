import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from fakediscord import payloads, settle
from voice_fakes import (
    BOT_ID,
    GUILD_ID,
    NO_BITRATE,
    OTHER_GUILD_ID,
    OTHER_VOICE,
    STAGE,
    TEXT,
    USER_ID,
    VOICE,
    FakeBot,
    connected,
    disconnected,
    ready,
    rest_voice_state,
    run,
    voice_state,
)
from voice_fakes import Call as CoreCall

from bostie import voice
from bostie.enums import Intents, SpeakingFlags
from bostie.errors import Forbidden, MissingIntent, NotFound
from bostie.events import GuildCreate, VoiceServerUpdate, VoiceStateUpdate
from bostie.models import VoiceState, decode
from bostie.voice.formats import StageRole

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

PATCH_ME = f"/guilds/{GUILD_ID}/voice-states/@me"


def test_connect_merges_each_argument_over_the_defaults() -> None:
    async def scenario(bot: FakeBot) -> None:
        await connected(
            bot,
            self_mute=False,
            timeout=3.0,
            server_change_delay=0.0,
            encoder=voice.EncoderSettings(
                bitrate=50_000,
                application="lowdelay",
                fec=False,
                packet_loss=0.29,
                complexity=0,
            ),
            speaking_flags=SpeakingFlags.SOUNDSHARE,
            speech_activity=False,
        )
        assert bot.calls("voice_connect") == [
            (
                "voice_connect",
                (GUILD_ID, VOICE),
                {
                    "shard_id": 0,
                    "user_id": BOT_ID,
                    "self_mute": False,
                    "self_deaf": True,
                    "timeout": 3.0,
                    "server_change_delay": 0.0,
                    "bitrate": 50_000,
                    "application": "lowdelay",
                    "fec": False,
                    "packet_loss": 29,
                    "complexity": 0,
                    "speaking": 2,
                },
            )
        ]
        assert [call[1] for call in bot.calls("voice_speech_activity")] == [
            (GUILD_ID, False)
        ]

    run(
        scenario,
        defaults=voice.Defaults(
            self_deaf=True,
            self_mute=True,
            timeout=15.0,
            server_change_delay=0.3,
            encoder=voice.EncoderSettings(application="voip"),
        ),
    )


@pytest.mark.parametrize(
    ("call", "option", "error"),
    [
        ("connect", "timeout", ValueError),
        ("connect", "jitter", TypeError),
        ("connect", "sink_buffer", TypeError),
        ("connect_external", "encoder", TypeError),
        ("context", "overflow", TypeError),
    ],
)
def test_invalid_connect_options_raise_before_anything_is_sent(
    call: str, option: str, error: type[Exception]
) -> None:
    options: dict[str, Any] = {option: 0.0}

    async def scenario(bot: FakeBot) -> None:
        attempts: dict[str, Callable[[], Awaitable[object]]] = {
            "connect": lambda: bot.voice.connect(VOICE, **options),
            "connect_external": lambda: bot.voice.connect_external(
                GUILD_ID, VOICE, **options
            ),
            "context": lambda: context_voice(bot).connect(**options),
        }
        with pytest.raises(error, match=option):
            await attempts[call]()
        assert (bot.core.sent, bot.rest.requests) == ([], [])

    run(scenario)


def test_bitrate_follows_the_channel_and_falls_back_to_64_kbps() -> None:
    async def scenario(bot: FakeBot) -> None:
        await connected(bot, VOICE)
        await connected(bot, NO_BITRATE, guild_id=OTHER_GUILD_ID)
        bitrates = [call[2]["bitrate"] for call in bot.calls("voice_connect")]
        assert bitrates == [96_000, 64_000]

    run(scenario)


@pytest.mark.parametrize("call", ["connect", "connect_external"])
def test_voice_needs_the_guild_voice_states_intent(call: str) -> None:
    async def scenario(bot: FakeBot) -> None:
        attempt = (
            bot.voice.connect(VOICE)
            if call == "connect"
            else bot.voice.connect_external(GUILD_ID, VOICE)
        )
        with pytest.raises(MissingIntent, match=r"Intents\.GUILD_VOICE_STATES"):
            await attempt
        assert (bot.core.sent, bot.rest.requests) == ([], [])

    run(scenario, intents=Intents.GUILDS)


def test_voice_needs_a_running_bot() -> None:
    async def scenario(bot: FakeBot) -> None:
        with pytest.raises(RuntimeError, match=r"bot\.voice\.connect\(\) needs a run"):
            await bot.voice.connect(VOICE)

    run(scenario, running=False)


def test_a_cached_channel_needs_no_request() -> None:
    async def scenario(bot: FakeBot) -> None:
        guild = payloads.guild(
            id=str(GUILD_ID),
            channels=[{"id": str(VOICE), "type": 2, "bitrate": 128_000}],
            threads=[],
        )
        bot.cache.apply(GuildCreate.decode(0, msgspec.json.encode(guild)))
        await connected(bot)
        assert bot.rest.requests == []
        assert bot.calls("voice_connect")[0][2]["bitrate"] == 128_000

    run(scenario)


@pytest.mark.parametrize("channel_id", [TEXT, 999])
def test_only_guild_voice_and_stage_channels_connect(channel_id: int) -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.rest.answers[("GET", "/channels/999")] = (200, {"id": "999", "type": 2})
        with pytest.raises(ValueError, match="is not a voice or stage channel"):
            await bot.voice.connect(channel_id)
        assert bot.core.sent == []

    run(scenario)


@pytest.mark.parametrize(
    ("default", "channel", "options", "patches"),
    [
        ("listen", VOICE, {"stage": "speak"}, []),
        ("listen", STAGE, {"stage": "speak"}, [{"suppress": False}]),
        ("listen", STAGE, {}, []),
        ("speak", STAGE, {}, [{"suppress": False}]),
        ("speak", STAGE, {"stage": "listen"}, []),
    ],
)
def test_a_first_connect_asks_for_the_stage_role_on_a_stage_channel(
    default: StageRole,
    channel: int,
    options: voice.ConnectOptions,
    patches: list[dict[str, bool]],
) -> None:
    async def scenario(bot: FakeBot) -> None:
        await connected(bot, channel, **options)
        sent = [request for request in bot.rest.requests if request.method == "PATCH"]
        assert [request.path for request in sent] == [PATCH_ME] * len(patches)
        assert [request.json for request in sent] == [
            {"channel_id": str(channel)} | patch for patch in patches
        ]

    run(scenario, defaults=voice.Defaults(stage=default))


def test_a_stage_request_patches_the_time_it_asked_to_speak() -> None:
    async def scenario(bot: FakeBot) -> None:
        before = datetime.now(UTC)
        await connected(bot, STAGE, stage="request")
        after = datetime.now(UTC)
        request = bot.rest.requests[-1]
        assert request.json.keys() == {"channel_id", "request_to_speak_timestamp"}
        asked = datetime.fromisoformat(request.json["request_to_speak_timestamp"])
        assert before <= asked <= after

    run(scenario)


def test_a_failing_stage_request_disconnects_and_raises() -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.rest.answers[("PATCH", PATCH_ME)] = (
            403,
            {"code": 50013, "message": "Missing Permissions"},
        )
        task = asyncio.ensure_future(bot.voice.connect(STAGE, stage="speak"))
        await settle()
        bot.deliver(ready())
        with pytest.raises(Forbidden):
            await task
        assert bot.core.sent == [voice_state(STAGE), voice_state(None)]
        assert bot.voice.get(GUILD_ID) is None

    run(scenario)


def test_a_second_connect_moves_the_connection_and_waits_for_ready() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot, VOICE)
        moving = asyncio.ensure_future(bot.voice.connect(OTHER_VOICE))
        await settle()
        bot.deliver(("voice_state", GUILD_ID, "reconnecting", None, None, "moving"))
        bot.deliver(("voice_moved", GUILD_ID, VOICE, OTHER_VOICE, True))
        await settle()
        assert not moving.done()
        bot.deliver(ready())
        assert await moving is vc
        assert bot.voice.connections == (vc,)
        assert bot.core.sent == [voice_state(VOICE), voice_state(OTHER_VOICE)]

    run(scenario)


@pytest.mark.parametrize(
    ("options", "applied"),
    [
        ({}, []),
        ({"self_deaf": True}, [("voice_update", (GUILD_ID, VOICE, True, True), {})]),
        (
            {"encoder": voice.EncoderSettings(application="voip")},
            [
                (
                    "voice_encoder",
                    (GUILD_ID,),
                    {
                        "bitrate": 96_000,
                        "application": "voip",
                        "fec": True,
                        "packet_loss": 10,
                        "complexity": 10,
                    },
                )
            ],
        ),
        (
            {"speaking_flags": SpeakingFlags.SOUNDSHARE},
            [("voice_speaking", (GUILD_ID, 2), {})],
        ),
        (
            {"speech_hangover": 0.5, "speech_activity": False},
            [
                ("voice_speech_hangover", (GUILD_ID, 0.5), {}),
                ("voice_speech_activity", (GUILD_ID, False), {}),
            ],
        ),
        (
            {"server_change_delay": 0.5},
            [("voice_server_change_delay", (GUILD_ID, 0.5), {})],
        ),
    ],
)
def test_a_second_connect_keeps_the_state_and_applies_only_its_options(
    options: voice.ConnectOptions, applied: list[CoreCall]
) -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot, self_mute=True)
        before = len(bot.core.calls)
        assert await bot.voice.connect(VOICE, **options) is vc
        assert bot.core.calls[before:] == applied

    run(scenario)


@pytest.mark.parametrize(
    ("default", "start", "entry", "options", "patches"),
    [
        ("listen", STAGE, "connect", {"stage": "speak"}, [{"suppress": False}]),
        ("speak", STAGE, "connect", {"stage": "listen"}, [{"suppress": True}]),
        ("speak", VOICE, "connect", {}, [{"suppress": False}]),
        ("speak", VOICE, "move", {}, [{"suppress": False}]),
        ("listen", VOICE, "move", {}, []),
    ],
)
def test_entering_a_stage_asks_for_the_connections_stage_role(
    default: StageRole,
    start: int,
    entry: str,
    options: voice.ConnectOptions,
    patches: list[dict[str, bool]],
) -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot, start)
        before = len(bot.rest.requests)
        task = asyncio.ensure_future(
            bot.voice.connect(STAGE, **options)
            if entry == "connect"
            else vc.move(STAGE)
        )
        await settle()
        if start != STAGE:
            bot.deliver(("voice_moved", GUILD_ID, start, STAGE, True))
            bot.deliver(ready())
        await task
        sent = [r for r in bot.rest.requests[before:] if r.method == "PATCH"]
        assert [r.json for r in sent] == [
            {"channel_id": str(STAGE)} | patch for patch in patches
        ]

    run(scenario, defaults=voice.Defaults(stage=default))


def test_concurrent_connects_both_get_the_ready_connection() -> None:
    async def scenario(bot: FakeBot) -> None:
        first = asyncio.ensure_future(bot.voice.connect(VOICE))
        second = asyncio.ensure_future(bot.voice.connect(VOICE))
        await settle()
        bot.deliver(ready())
        one, two = await asyncio.gather(first, second)
        assert one is two
        assert bot.core.sent == [voice_state(VOICE)]

    run(scenario)


def test_concurrent_connects_both_raise_when_the_attempt_fails() -> None:
    async def scenario(bot: FakeBot) -> None:
        first = asyncio.ensure_future(bot.voice.connect(VOICE))
        second = asyncio.ensure_future(bot.voice.connect(VOICE))
        await settle()
        bot.deliver(disconnected("timeout", detail="waiting for VOICE_SERVER_UPDATE"))
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert [type(result) for result in results] == [voice.VoiceConnectError] * 2

    run(scenario)


def test_a_failed_connect_raises_with_reason_code_and_detail() -> None:
    async def scenario(bot: FakeBot) -> None:
        task = asyncio.ensure_future(bot.voice.connect(VOICE))
        await settle()
        bot.deliver(disconnected("authentication_failed", 4004, "rejoin limit reached"))
        with pytest.raises(voice.VoiceConnectError) as raised:
            await task
        error = raised.value
        assert (error.reason, error.code, error.detail) == (
            voice.DisconnectReason.AUTHENTICATION_FAILED,
            4004,
            "rejoin limit reached",
        )
        assert bot.voice.get(GUILD_ID) is None

    run(scenario)


def test_cancelling_a_connect_disconnects() -> None:
    async def scenario(bot: FakeBot) -> None:
        task = asyncio.ensure_future(bot.voice.connect(VOICE))
        await settle()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await settle()
        assert bot.core.sent == [voice_state(VOICE), voice_state(None)]
        assert bot.voice.connections == ()

    run(scenario)


def voice_state_update(channel_id: int, user_id: int = BOT_ID) -> VoiceStateUpdate:
    state = msgspec.json.encode(rest_voice_state(channel_id, user_id))
    return VoiceStateUpdate(voice_state=decode(VoiceState, state))


def test_connect_external_returns_the_state_and_server_and_disconnects() -> None:
    async def scenario(bot: FakeBot) -> None:
        task = asyncio.ensure_future(bot.voice.connect_external(GUILD_ID, VOICE))
        await settle()
        assert bot.core.sent == [voice_state(VOICE)]
        bot.dispatch(voice_state_update(VOICE, user_id=USER_ID))
        bot.dispatch(
            VoiceServerUpdate(guild_id=OTHER_GUILD_ID, token="other", endpoint=None)
        )
        bot.dispatch(voice_state_update(VOICE))
        bot.dispatch(
            VoiceServerUpdate(guild_id=GUILD_ID, token="t", endpoint="v.discord.gg")
        )
        external = await task
        assert external.state.voice_state.session_id == "abc"
        assert (external.server.token, external.server.endpoint) == (
            "t",
            "v.discord.gg",
        )
        await external.disconnect()
        assert bot.core.sent[-1] == voice_state(None)
        assert bot.voice.get(GUILD_ID) is None

    run(scenario)


def test_connect_external_refuses_a_guild_bostie_already_runs() -> None:
    async def scenario(bot: FakeBot) -> None:
        await connected(bot)
        with pytest.raises(RuntimeError, match="already runs a voice connection"):
            await bot.voice.connect_external(GUILD_ID, VOICE)

    run(scenario)


def test_connect_external_timing_out_leaves_the_channel() -> None:
    async def scenario(bot: FakeBot) -> None:
        with pytest.raises(voice.VoiceConnectError) as raised:
            await bot.voice.connect_external(GUILD_ID, VOICE, timeout=0.05)
        assert raised.value.reason is voice.DisconnectReason.TIMEOUT
        assert bot.core.sent == [voice_state(VOICE), voice_state(None)]

    run(scenario)


def context_voice(bot: FakeBot, guild_id: int | None = GUILD_ID) -> voice.ContextVoice:
    return voice.ContextVoice(bot.voice, guild_id, USER_ID)


def test_context_voice_connects_to_the_users_channel() -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.rest.answers[("GET", f"/guilds/{GUILD_ID}/voice-states/{USER_ID}")] = (
            200,
            rest_voice_state(OTHER_VOICE),
        )
        ctx = context_voice(bot)
        before = ctx.current
        assert before is None
        task = asyncio.ensure_future(ctx.connect(self_deaf=True))
        await settle()
        bot.deliver(ready())
        vc = await task
        assert (vc.channel_id, ctx.current) == (OTHER_VOICE, vc)
        assert bot.core.sent == [voice_state(OTHER_VOICE, self_deaf=True)]
        assert context_voice(bot, None).current is None

    run(scenario)


@pytest.mark.parametrize("case", ["dm", "unknown voice state", "no channel"])
def test_context_voice_raises_not_in_voice(case: str) -> None:
    async def scenario(bot: FakeBot) -> None:
        route = ("GET", f"/guilds/{GUILD_ID}/voice-states/{USER_ID}")
        if case == "unknown voice state":
            bot.rest.answers[route] = (404, {"code": 10065, "message": "Unknown"})
        elif case == "no channel":
            bot.rest.answers[route] = (200, rest_voice_state(None))
        guild_id = None if case == "dm" else GUILD_ID
        with pytest.raises(voice.NotInVoice):
            await context_voice(bot, guild_id).connect()
        assert bot.core.sent == []

    run(scenario)


def test_context_voice_lets_other_not_found_errors_through() -> None:
    async def scenario(bot: FakeBot) -> None:
        with pytest.raises(NotFound):
            await context_voice(bot).connect()

    run(scenario)
