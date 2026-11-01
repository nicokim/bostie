import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime

import msgspec
import pytest
from fakediscord import payloads
from voice_fakes import (
    BOT_ID,
    ELSEWHERE,
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
    settle,
    voice_state,
)

from bostie import voice
from bostie.enums import Intents, SpeakingFlags
from bostie.errors import Forbidden, NotFound
from bostie.events import GuildCreate, VoiceServerUpdate, VoiceStateUpdate
from bostie.models import VoiceState, decode
from bostie.voice import connection as connection_module

PATCH_ME = f"/guilds/{GUILD_ID}/voice-states/@me"


def test_connect_merges_each_argument_over_the_defaults() -> None:
    async def scenario(bot: FakeBot) -> None:
        await connected(
            bot,
            self_mute=True,
            server_change_delay=0.5,
            encoder=voice.EncoderSettings(
                bitrate=50_000, packet_loss=0.25, complexity=7
            ),
            speaking_flags=SpeakingFlags.SOUNDSHARE,
        )
        assert bot.calls("voice_connect") == [
            (
                "voice_connect",
                (GUILD_ID, VOICE),
                {
                    "shard_id": 0,
                    "user_id": BOT_ID,
                    "self_mute": True,
                    "self_deaf": True,
                    "timeout": 15.0,
                    "server_change_delay": 0.5,
                    "bitrate": 50_000,
                    "application": "audio",
                    "fec": True,
                    "packet_loss": 25,
                    "complexity": 7,
                    "speaking": 2,
                },
            )
        ]

    run(
        scenario,
        defaults=voice.Defaults(
            self_deaf=True,
            timeout=15.0,
            encoder=voice.EncoderSettings(application="voip"),
        ),
    )


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
        with pytest.raises(RuntimeError, match=r"Intents\.GUILD_VOICE_STATES"):
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


def test_an_uncached_channel_is_fetched() -> None:
    async def scenario(bot: FakeBot) -> None:
        await connected(bot)
        assert bot.rest.paths("GET") == [f"/channels/{VOICE}"]

    run(scenario)


@pytest.mark.parametrize("channel_id", [TEXT, 999])
def test_only_guild_voice_and_stage_channels_connect(channel_id: int) -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.rest.answers[("GET", "/channels/999")] = (200, {"id": "999", "type": 2})
        with pytest.raises(ValueError, match="is not a voice or stage channel"):
            await bot.voice.connect(channel_id)
        assert bot.core.sent == []

    run(scenario)


def test_stage_is_ignored_on_a_voice_channel() -> None:
    async def scenario(bot: FakeBot) -> None:
        await connected(bot, VOICE, stage="speak")
        assert bot.rest.paths("PATCH") == []

    run(scenario)


def test_stage_speak_and_request_patch_the_bots_voice_state() -> None:
    async def scenario(bot: FakeBot) -> None:
        speaking = await connected(bot, STAGE, stage="speak")
        speak = bot.rest.requests[-1]
        assert (speak.method, speak.path, speak.json) == (
            "PATCH",
            PATCH_ME,
            {"channel_id": str(STAGE), "suppress": False},
        )
        await speaking.disconnect()
        listening = await connected(bot, STAGE)
        assert bot.rest.paths("PATCH") == [PATCH_ME]
        await listening.disconnect()
        await connected(bot, STAGE, stage="request")
        request = bot.rest.requests[-1]
        assert request.json.keys() == {"channel_id", "request_to_speak_timestamp"}
        assert datetime.fromisoformat(request.json["request_to_speak_timestamp"])

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


def test_vc_stage_works_only_on_a_stage_channel() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot, VOICE)
        with pytest.raises(ValueError, match="is not a stage channel"):
            await vc.stage("speak")
        await vc.disconnect()
        await (await connected(bot, STAGE)).stage("listen")
        assert bot.rest.requests[-1].json == {
            "channel_id": str(STAGE),
            "suppress": True,
        }

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


def test_a_second_connect_with_other_flags_edits_them() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot, VOICE)
        assert await bot.voice.connect(VOICE) is vc
        assert await bot.voice.connect(VOICE, self_deaf=True) is vc
        assert bot.core.sent == [voice_state(VOICE), voice_state(VOICE, self_deaf=True)]

    run(scenario)


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


def test_wait_until_sees_every_state_and_raises_when_the_connection_ends() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        resuming = asyncio.ensure_future(vc.wait_until(voice.State.RESUMING))
        await settle()
        bot.deliver(("voice_state", GUILD_ID, "resuming", None, None, "ws lost"))
        bot.deliver(ready())
        await resuming
        assert vc.state is voice.State.READY
        reconnecting = asyncio.ensure_future(vc.wait_until(voice.State.RECONNECTING))
        ended = asyncio.ensure_future(vc.wait_until(voice.State.DISCONNECTED))
        await settle()
        bot.deliver(disconnected("removed"))
        with pytest.raises(voice.VoiceNotConnected):
            await reconnecting
        await ended
        await vc.wait_until(voice.State.DISCONNECTED)

    run(scenario)


def test_every_voice_delivery_updates_the_connection_and_emits_its_event() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        bot.deliver(("voice_dave", GUILD_ID, 1, 7, 3))
        bot.deliver(("voice_speaking", GUILD_ID, USER_ID, 4242, 5))
        bot.deliver(("voice_clients", GUILD_ID, [USER_ID, USER_ID + 1, USER_ID]))
        bot.deliver(("voice_client_left", GUILD_ID, USER_ID))
        bot.deliver(("voice_stalled", GUILD_ID, "send", True, 2.0))
        bot.deliver(("voice_stalled", GUILD_ID, "send", False, 3.5))
        bot.deliver(("voice_moved", GUILD_ID, VOICE, OTHER_VOICE, False))
        assert vc.channel_id == OTHER_VOICE
        assert bot.events == [
            voice.StateChange(
                guild_id=GUILD_ID,
                old=voice.State.CONNECTING,
                new=voice.State.READY,
                reason=None,
                code=None,
                detail="",
            ),
            voice.DaveTransition(
                guild_id=GUILD_ID, protocol_version=1, epoch=7, transition_id=3
            ),
            voice.SpeakingUpdate(
                guild_id=GUILD_ID,
                user_id=USER_ID,
                ssrc=4242,
                flags=SpeakingFlags.MICROPHONE | SpeakingFlags.PRIORITY,
            ),
            voice.UserJoin(guild_id=GUILD_ID, user_id=USER_ID),
            voice.UserJoin(guild_id=GUILD_ID, user_id=USER_ID + 1),
            voice.UserLeave(guild_id=GUILD_ID, user_id=USER_ID),
            voice.MediaStalled(
                guild_id=GUILD_ID, direction=voice.Direction.SEND, duration=2.0
            ),
            voice.MediaResumed(
                guild_id=GUILD_ID, direction=voice.Direction.SEND, duration=3.5
            ),
            voice.Moved(
                guild_id=GUILD_ID,
                old_channel_id=VOICE,
                new_channel_id=OTHER_VOICE,
                by_us=False,
            ),
        ]

    run(scenario)


def test_members_follow_joins_and_leaves_and_a_known_member_joins_once() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        bot.deliver(("voice_clients", GUILD_ID, [USER_ID, USER_ID + 1]))
        bot.deliver(("voice_client_left", GUILD_ID, USER_ID + 1))
        bot.deliver(("voice_clients", GUILD_ID, [USER_ID, USER_ID + 2]))
        assert vc.members == {USER_ID, USER_ID + 2}
        joins = [event.user_id for event in bot.events_of(voice.UserJoin)]
        assert joins == [USER_ID, USER_ID + 1, USER_ID + 2]
        bot.deliver(("voice_moved", GUILD_ID, VOICE, OTHER_VOICE, True))
        assert vc.members == frozenset()

    run(scenario)


def test_voice_self_shows_what_discord_applied() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot, self_mute=True)
        assert (vc.self_mute, vc.self_deaf, vc.mute, vc.suppress) == (
            True,
            False,
            False,
            False,
        )
        bot.deliver(("voice_self", GUILD_ID, False, True, True, True))
        assert (vc.self_mute, vc.self_deaf, vc.mute, vc.suppress) == (
            False,
            True,
            True,
            True,
        )

    run(scenario)


@pytest.mark.parametrize(
    ("mute", "suppress", "warning"),
    [
        (True, False, "it is server muted"),
        (False, True, "it is suppressed on this stage; use vc.stage('request')"),
    ],
)
def test_audio_into_a_muted_connection_warns_once(
    caplog: pytest.LogCaptureFixture, mute: bool, suppress: bool, warning: str
) -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        await vc.sink().write(b"\x00\x00\x00\x00")
        assert caplog.records == []
        bot.deliver(("voice_self", GUILD_ID, False, False, mute, suppress))
        first, second = vc.sink(), vc.sink()
        await first.write(b"\x00\x00\x00\x00")
        await first.write(b"\x00\x00\x00\x00")
        await second.write(b"\x00\x00\x00\x00")

    with caplog.at_level(logging.WARNING, "bostie.voice"):
        run(scenario)
    assert [record.getMessage() for record in caplog.records] == [
        f"nobody hears the bot in guild {GUILD_ID}: {warning}"
    ]


type Call = Callable[[voice.VoiceConnection], Awaitable[None]]

AFTER_THE_END: dict[str, Call] = {}


def after_the_end(name: str) -> Callable[[Call], Call]:
    def register(call: Call) -> Call:
        AFTER_THE_END[name] = call
        return call

    return register


@after_the_end("sink")
async def open_a_sink(vc: voice.VoiceConnection) -> None:
    vc.sink()


@after_the_end("play")
async def play(vc: voice.VoiceConnection) -> None:
    await vc.play(voice.File("a.ogg"))


@after_the_end("move")
async def move(vc: voice.VoiceConnection) -> None:
    await vc.move(OTHER_VOICE)


@after_the_end("edit")
async def edit(vc: voice.VoiceConnection) -> None:
    await vc.edit(self_mute=True)


@after_the_end("stage")
async def stage(vc: voice.VoiceConnection) -> None:
    await vc.stage("speak")


@after_the_end("reconnect")
async def reconnect(vc: voice.VoiceConnection) -> None:
    vc.reconnect()


@after_the_end("encoder")
async def set_encoder(vc: voice.VoiceConnection) -> None:
    vc.encoder = voice.EncoderSettings(bitrate=96_000)


@after_the_end("speaking_flags")
async def set_speaking_flags(vc: voice.VoiceConnection) -> None:
    vc.speaking_flags = SpeakingFlags.SOUNDSHARE


@after_the_end("wait_until")
async def wait_until_ready(vc: voice.VoiceConnection) -> None:
    await vc.wait_until(voice.State.READY)


@after_the_end("verification_code")
async def verification_code(vc: voice.VoiceConnection) -> None:
    await vc.dave.verification_code(USER_ID)


@pytest.mark.parametrize("name", AFTER_THE_END)
def test_after_the_end_calls_raise_voice_not_connected(name: str) -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        dave = vc.dave
        bot.deliver(disconnected("removed"))
        with pytest.raises(voice.VoiceNotConnected):
            await AFTER_THE_END[name](vc)
        assert vc.dave == dave

    run(scenario)


def test_after_the_end_reads_return_the_last_values() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        dave = vc.dave
        bot.deliver(disconnected("requested"))
        await vc.disconnect()
        assert vc.latency == voice.Latency(None, None)
        assert vc.dave == dave
        assert bot.voice.connections == ()

    run(scenario)


def test_live_reads_come_from_the_core() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        assert vc.latency == voice.Latency(0.01, 0.02)
        assert (vc.dave.protocol_version, vc.dave.epoch, vc.dave.privacy_code) == (
            1,
            4,
            "11111 22222",
        )
        code = await vc.dave.verification_code(USER_ID)
        assert code.startswith(f"code for {USER_ID} on ")
        assert not code.endswith(threading.main_thread().name)

    run(scenario)


def test_assigning_an_encoder_applies_it_with_the_channels_bitrate() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        vc.encoder = voice.EncoderSettings(
            application="voip", packet_loss=0.0, complexity=5
        )
        assert vc.encoder == voice.EncoderSettings(
            application="voip", packet_loss=0.0, complexity=5
        )
        assert bot.calls("voice_encoder") == [
            (
                "voice_encoder",
                (GUILD_ID,),
                {
                    "bitrate": 96_000,
                    "application": "voip",
                    "fec": True,
                    "packet_loss": 0,
                    "complexity": 5,
                },
            )
        ]

    run(scenario)


def test_a_move_reapplies_the_new_channels_bitrate_only_when_following_it() -> None:
    async def scenario(bot: FakeBot) -> None:
        following = await connected(bot)
        fixed = await connected(
            bot,
            NO_BITRATE,
            guild_id=OTHER_GUILD_ID,
            encoder=voice.EncoderSettings(bitrate=50_000),
        )
        bot.deliver(("voice_clients", GUILD_ID, [USER_ID]))
        bot.deliver(ready())
        await settle()
        assert bot.calls("voice_encoder") == []
        bot.deliver(("voice_moved", GUILD_ID, VOICE, OTHER_VOICE, False))
        bot.deliver(("voice_moved", OTHER_GUILD_ID, NO_BITRATE, ELSEWHERE, False))
        await settle()
        assert [call[1:] for call in bot.calls("voice_encoder")] == [
            (
                (GUILD_ID,),
                {
                    "bitrate": 32_000,
                    "application": "audio",
                    "fec": True,
                    "packet_loss": 10,
                    "complexity": 10,
                },
            )
        ]
        assert (following.channel_id, fixed.channel_id) == (OTHER_VOICE, ELSEWHERE)

    run(scenario)


def test_speaking_flags_go_to_the_core_and_never_zero() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        with pytest.raises(ValueError, match="at least one flag"):
            vc.speaking_flags = SpeakingFlags(0)
        vc.speaking_flags = SpeakingFlags.SOUNDSHARE
        assert vc.speaking_flags is SpeakingFlags.SOUNDSHARE
        assert bot.calls("voice_speaking") == [("voice_speaking", (GUILD_ID, 2), {})]

    run(scenario)


def test_move_waits_for_the_new_channel_and_ready() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot, self_deaf=True)
        moving = asyncio.ensure_future(vc.move(OTHER_VOICE))
        await settle()
        assert bot.core.sent[-1] == voice_state(OTHER_VOICE, self_deaf=True)
        bot.deliver(ready())
        await settle()
        assert not moving.done()
        bot.deliver(("voice_moved", GUILD_ID, VOICE, OTHER_VOICE, True))
        await moving
        assert vc.channel_id == OTHER_VOICE

    run(scenario)


def test_a_move_that_times_out_keeps_the_connection_where_it_was() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        with pytest.raises(voice.VoiceConnectError) as raised:
            await vc.move(OTHER_VOICE, timeout=0.05)
        assert raised.value.reason is voice.DisconnectReason.TIMEOUT
        assert raised.value.detail == (
            f"no move to {OTHER_VOICE}; the channel may be full or the bot lacks "
            "CONNECT"
        )
        assert (vc.channel_id, vc.state) == (VOICE, voice.State.READY)

    run(scenario)


def test_a_move_to_another_guilds_channel_is_refused() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        with pytest.raises(ValueError, match="in another guild"):
            await vc.move(ELSEWHERE)
        assert bot.core.sent == [voice_state(VOICE)]

    run(scenario)


def test_edit_sends_the_flags_for_the_same_channel() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot, self_deaf=True)
        await vc.edit(self_mute=True)
        await vc.edit(self_deaf=False)
        assert bot.core.sent[1:] == [
            voice_state(VOICE, self_mute=True, self_deaf=True),
            voice_state(VOICE, self_mute=True),
        ]

    run(scenario)


def test_disconnect_returns_once_ended_and_is_idempotent() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        await vc.disconnect()
        await vc.disconnect()
        assert vc.state is voice.State.DISCONNECTED
        assert bot.core.sent == [voice_state(VOICE), voice_state(None)]
        assert bot.events[-1] == voice.StateChange(
            guild_id=GUILD_ID,
            old=voice.State.READY,
            new=voice.State.DISCONNECTED,
            reason=voice.DisconnectReason.REQUESTED,
            code=None,
            detail="",
        )

    run(scenario)


def test_close_disconnects_every_connection_and_ends_the_silent_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection_module, "CLOSE_TIMEOUT", 0.05)

    async def scenario(bot: FakeBot) -> None:
        answering = await connected(bot)
        await connected(bot, NO_BITRATE, guild_id=OTHER_GUILD_ID)
        silent = bot.voice.get(OTHER_GUILD_ID)
        original = bot.core.voice_disconnect

        def disconnect(guild_id: int) -> None:
            bot.core.auto_disconnect = guild_id == GUILD_ID
            original(guild_id)

        monkeypatch.setattr(bot.core, "voice_disconnect", disconnect)
        await bot.voice.close()
        assert bot.voice.connections == ()
        assert answering.state is voice.State.DISCONNECTED
        assert silent is not None
        assert silent.state is voice.State.DISCONNECTED
        endings = [
            (event.guild_id, event.reason)
            for event in bot.events_of(voice.StateChange)
            if event.new is voice.State.DISCONNECTED
        ]
        assert sorted(endings) == [
            (GUILD_ID, voice.DisconnectReason.REQUESTED),
            (OTHER_GUILD_ID, voice.DisconnectReason.CLOSED),
        ]

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


def test_play_opens_a_sink_with_its_options_and_the_connection_defaults() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        track = await vc.play(voice.File("a.ogg"), volume=0.5, stall="drop", meta=7)
        sink = track.sink
        assert (track.meta, sink.volume, sink.stall, sink.buffer) == (
            7,
            0.5,
            "drop",
            0.4,
        )
        track.stop()

    run(scenario, defaults=voice.Defaults(sink_buffer=0.4))


def test_handles_of_unknown_ids_and_guilds_are_ignored() -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.deliver(("voice_wake", 12345))
        bot.deliver(("voice_marker", 12345, 1, True))
        bot.deliver(ready(guild_id=999))
        assert bot.events == []

    run(scenario)
