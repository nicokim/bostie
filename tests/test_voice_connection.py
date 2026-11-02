import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fakediscord import settle
from voice_fakes import (
    ELSEWHERE,
    GUILD_ID,
    NO_BITRATE,
    OTHER_GUILD_ID,
    OTHER_VOICE,
    STAGE,
    USER_ID,
    VOICE,
    FakeBot,
    connected,
    disconnected,
    ready,
    run,
    voice_state,
)

from bostie import voice
from bostie.enums import SpeakingFlags
from bostie.voice import connection as connection_module

if TYPE_CHECKING:
    from bostie._native import RtcpPacketItem


async def awaited(result: object) -> None:
    if inspect.isawaitable(result):
        await result


@pytest.mark.parametrize(
    ("call", "option"),
    [
        ("sink", "jitter"),
        ("play", "overflow"),
        ("queue", "timeout"),
        ("receive", "volume"),
        ("packets", "jitter"),
        ("move", "self_mute"),
        ("edit", "stage"),
    ],
)
def test_options_a_call_does_not_take_raise_type_error(call: str, option: str) -> None:
    options: dict[str, Any] = {option: 0.1}

    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        attempts: dict[str, Callable[[], object]] = {
            "sink": lambda: vc.sink(**options),
            "play": lambda: vc.play(voice.File("a.ogg"), **options),
            "queue": lambda: voice.Queue(vc, **options),
            "receive": lambda: vc.receive(**options),
            "packets": lambda: vc.receive(voice.Packets(), **options),
            "move": lambda: vc.move(OTHER_VOICE, **options),
            "edit": lambda: vc.edit(**options),
        }
        with pytest.raises(TypeError, match=option):
            await awaited(attempts[call]())
        assert bot.calls("voice_file") == bot.calls("voice_update") == []

    run(scenario)


def test_vc_stage_works_only_on_a_stage_channel() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot, VOICE)
        with pytest.raises(ValueError, match="is not a stage channel"):
            await vc.stage("speak")
        await vc.disconnect()
        await (await connected(bot, STAGE)).stage("listen")
        assert bot.api.requests[-1].json == {
            "channel_id": STAGE,
            "suppress": True,
        }

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
        report = (4242, USER_ID, 64, -2, 70_000, 480, 7, 32_768)
        packets: list[RtcpPacketItem] = [(201, 1, 1, None, [report], b"rr")]
        bot.deliver(("voice_rtcp", GUILD_ID, packets))
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
            voice.RtcpPacket(
                guild_id=GUILD_ID,
                packet_type=201,
                count=1,
                ssrc=1,
                user_id=None,
                reports=(
                    voice.ReceptionReport(
                        4242, USER_ID, 0.25, -2, 70_000, 0.01, 7, 0.5
                    ),
                ),
                data=b"rr",
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


def set_encoder(vc: voice.VoiceConnection) -> None:
    vc.encoder = voice.EncoderSettings(bitrate=96_000)


def set_speaking_flags(vc: voice.VoiceConnection) -> None:
    vc.speaking_flags = SpeakingFlags.SOUNDSHARE


def set_speech_hangover(vc: voice.VoiceConnection) -> None:
    vc.speech_hangover = 1.0


def set_speech_activity(vc: voice.VoiceConnection) -> None:
    vc.speech_activity = False


AFTER_THE_END: dict[str, Callable[[voice.VoiceConnection], object]] = {
    "sink": lambda vc: vc.sink(),
    "play": lambda vc: vc.play(voice.File("a.ogg")),
    "move": lambda vc: vc.move(OTHER_VOICE),
    "edit": lambda vc: vc.edit(self_mute=True),
    "stage": lambda vc: vc.stage("speak"),
    "reconnect": lambda vc: vc.reconnect(),
    "encoder": set_encoder,
    "speaking_flags": set_speaking_flags,
    "receive": lambda vc: vc.receive(),
    "speech_hangover": set_speech_hangover,
    "speech_activity": set_speech_activity,
    "wait_until": lambda vc: vc.wait_until(voice.State.READY),
    "verification_code": lambda vc: vc.dave.verification_code(USER_ID),
}


@pytest.mark.parametrize("name", AFTER_THE_END)
def test_after_the_end_calls_raise_voice_not_connected(name: str) -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        dave = vc.dave
        bot.deliver(disconnected("removed"))
        with pytest.raises(voice.VoiceNotConnected):
            await awaited(AFTER_THE_END[name](vc))
        assert vc.dave == dave

    run(scenario)


def test_after_the_end_reads_return_the_last_values() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        bot.core.ssrc_map = {4242: USER_ID}
        ssrcs, dave = vc.ssrcs, vc.dave
        bot.deliver(disconnected("requested"))
        await vc.disconnect()
        assert vc.latency == voice.Latency(None, None)
        assert (vc.ssrcs, vc.dave) == (ssrcs, dave)
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
        encoder = voice.EncoderSettings(
            application="voip", fec=False, packet_loss=1.0, complexity=5
        )
        vc.encoder = encoder
        assert vc.encoder == encoder
        assert bot.calls("voice_encoder") == [
            (
                "voice_encoder",
                (GUILD_ID,),
                {
                    "bitrate": 96_000,
                    "application": "voip",
                    "fec": False,
                    "packet_loss": 100,
                    "complexity": 5,
                    "bandwidth": "auto",
                    "signal": "auto",
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
                    "bandwidth": "auto",
                    "signal": "auto",
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


def test_edit_returns_once_discord_applied_it_or_raises_on_timeout() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        bot.core.applies_self = False
        editing = asyncio.ensure_future(vc.edit(self_deaf=True))
        await settle()
        assert not editing.done()
        bot.deliver(("voice_self", GUILD_ID, False, True, False, False))
        await editing
        assert vc.self_deaf
        with pytest.raises(TimeoutError):
            await vc.edit(self_mute=True, timeout=0.01)

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


def test_handles_of_unknown_ids_and_guilds_are_ignored() -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.deliver(("voice_wake", 12345))
        bot.deliver(("voice_marker", 12345, 1, True))
        bot.deliver(ready(guild_id=999))
        assert bot.events == []

    run(scenario)
