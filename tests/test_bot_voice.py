import asyncio
import contextlib
import itertools
import logging
import math
import struct
import threading
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable
from pathlib import Path
from typing import Any, Literal

import pytest
from fakediscord import FakeDiscord, Packet, payloads, running
from fakediscord.payloads import Json

from bostie import Bot, Context, Forbidden, Intents, voice
from bostie.enums import SpeakingFlags
from bostie.gateway import GatewayClient
from bostie.voice import (
    DisconnectReason,
    EndReason,
    NotInVoice,
    SinkClosed,
    State,
    VoiceConnectError,
    VoiceNotConnected,
)

type Scenario = Callable[[FakeDiscord], Coroutine[Any, Any, None]]

MEDIA = Path(__file__).parents[1] / "crates/bostie-core/src/voice/testdata/media"
GUILD_ID = int(payloads.voice_channel()["guild_id"])
VOICE_ID = int(payloads.voice_channel()["id"])
OTHER_ID = VOICE_ID + 1
STAGE_ID = int(payloads.STAGE_CHANNEL_ID)
MEMBER_ID = "613374681415811073"
FRIEND_ID = 1_100_000_000_000_000_001
SILENCE = b"\xf8\xff\xfe"
SILENT_TAIL = [SILENCE] * 5
FRAMES_PER_SECOND = 50
SAMPLES_PER_FRAME = 960
TWENTY_MS_CONFIGS = frozenset({3, 7, 11, 13, 15, 19, 23, 27, 31})
MAX_OPUS_FRAME = 1275
TONE_FRAME = struct.pack(
    f"<{SAMPLES_PER_FRAME * 2}h",
    *(
        int(8000 * math.sin(2 * math.pi * 400 * index / 48_000))
        for index in range(SAMPLES_PER_FRAME)
        for _ in range(2)
    ),
)


def run(scenario: Scenario, **options: Any) -> None:
    async def main() -> None:
        async with FakeDiscord(voice=True, **options) as fake, asyncio.timeout(30):
            await scenario(fake)

    asyncio.run(main())


@contextlib.asynccontextmanager
async def started(fake: FakeDiscord, **options: Any) -> AsyncIterator[Bot]:
    bot = Bot(api_url=fake.api_url, **options)
    async with running(bot, fake):
        yield bot


def tone(seconds: float) -> bytes:
    return TONE_FRAME * round(seconds * FRAMES_PER_SECOND)


def is_opus_frame(payload: bytes) -> bool:
    return (
        0 < len(payload) <= MAX_OPUS_FRAME
        and payload[0] >> 3 in TWENTY_MS_CONFIGS
        and payload[0] & 3 == 0
    )


def assert_contiguous(packets: Iterable[Packet]) -> None:
    for before, after in itertools.pairwise(packets):
        assert (after.sequence - before.sequence) % 2**16 == 1
        assert (after.timestamp - before.timestamp) % 2**32 == SAMPLES_PER_FRAME


async def played(fake: FakeDiscord, start: int = 0) -> list[Packet]:
    count = start + len(SILENT_TAIL)
    while True:
        packets = (await fake.wait_for_packets(count))[start:]
        if [packet.payload for packet in packets[-len(SILENT_TAIL) :]] == SILENT_TAIL:
            return packets
        count += 1


def sent(fake: FakeDiscord, op: int) -> list[Any]:
    payloads = []
    while not fake.gateway_received.empty():
        payload = fake.gateway_received.get_nowait()
        if payload["op"] == op:
            payloads.append(payload["d"])
    return payloads


def voice_ops(fake: FakeDiscord, op: int) -> list[Any]:
    found = []
    while not fake.voice_received.empty():
        payload = fake.voice_received.get_nowait()
        if isinstance(payload, dict) and payload["op"] == op:
            found.append(payload["d"])
    return found


def join(channel_id: int, *, self_mute: bool = False, self_deaf: bool = False) -> Json:
    return {
        "guild_id": str(GUILD_ID),
        "channel_id": str(channel_id),
        "self_mute": self_mute,
        "self_deaf": self_deaf,
    }


LEAVE = {
    "guild_id": str(GUILD_ID),
    "channel_id": None,
    "self_mute": False,
    "self_deaf": False,
}


def add_voice_channel(fake: FakeDiscord, channel_id: int) -> None:
    fake.channels[str(channel_id)] = payloads.voice_channel(
        id=str(channel_id), name="Second Lounge"
    )


async def opus_packets(bot: Bot, path: Path) -> list[bytes]:
    source = voice.File(path)
    await source.open(bot.voice)
    try:
        return [packet async for packet in source]
    finally:
        await source.aclose()


def test_connect_joins_with_string_snowflakes_and_identifies_for_dave() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            identify = await fake.next_voice_identify()

            assert sent(fake, 4) == [join(VOICE_ID)]
            bot_state = fake.voice_states[(str(GUILD_ID), fake.bot_user["id"])]
            assert identify == {
                "server_id": str(GUILD_ID),
                "user_id": fake.bot_user["id"],
                "session_id": bot_state["session_id"],
                "token": identify["token"],
                "max_dave_protocol_version": 1,
            }
            assert [session.mode for session in fake.voice_sessions] == [
                "aead_aes256_gcm_rtpsize"
            ]
            assert vc.state is State.READY
            assert vc.dave.protocol_version == 0
            assert (vc.guild_id, vc.channel_id) == (GUILD_ID, VOICE_ID)
            assert bot.voice.connections == (vc,)
            assert bot.voice.get(GUILD_ID) is vc

    run(scenario)


def test_a_second_of_pcm_goes_out_as_fifty_opus_packets_then_five_silent_ones() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            async with vc.sink(buffer=2.0) as out:
                out.write_nowait(tone(1.0))
                await fake.wait_for_packets(1)
                speaking = voice_ops(fake, 5)
            packets = await played(fake)

            ssrc = fake.voice_sessions[-1].ssrc
            assert speaking[0] == {"speaking": 1, "delay": 0, "ssrc": ssrc}
            assert len(packets) == FRAMES_PER_SECOND + len(SILENT_TAIL)
            assert all(
                is_opus_frame(packet.payload) and packet.payload != SILENCE
                for packet in packets[:FRAMES_PER_SECOND]
            )
            assert {packet.ssrc for packet in packets} == {ssrc}
            assert_contiguous(packets)
            stats = vc.stats().send
            assert (stats.encoded_packets, stats.silence_packets) == (50, 5)

    run(scenario)


def test_a_sink_that_plays_to_its_end_counts_no_underrun() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            async with vc.sink() as out:
                await out.write(tone(0.2))
                await out.drain()
                await played(fake)

            assert out.stats().underruns == 0

    run(scenario)


def test_opus_passes_through_byte_for_byte_unless_the_volume_changes() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            encoder = voice.OpusEncoder()
            encoded = encoder.encode(tone(1.0)) + encoder.flush()
            async with vc.sink(voice.Opus(), buffer=2.0) as out:
                for packet in encoded:
                    out.write_nowait(packet)
                await out.drain()
                assert out.passthrough
            first = await played(fake)
            assert [packet.payload for packet in first] == encoded + SILENT_TAIL
            assert out.stats().passthrough_packets == len(encoded)

            async with vc.sink(voice.Opus(), buffer=2.0, volume=0.5) as quieter:
                for packet in encoded:
                    quieter.write_nowait(packet)
                await quieter.drain()
                assert not quieter.passthrough
            replayed = (await played(fake, len(first)))[: len(encoded)]
            assert not {packet.payload for packet in replayed} & set(encoded)
            assert all(is_opus_frame(packet.payload) for packet in replayed)
            assert vc.stats().send.passthrough_packets == len(encoded)

    run(scenario)


def test_two_sinks_mix_into_one_packet_per_tick() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            encoder = voice.OpusEncoder()
            encoded = encoder.encode(tone(1.0)) + encoder.flush()

            pcm_frames = FRAMES_PER_SECOND + 10
            opus = vc.sink(voice.Opus(), buffer=2.0)
            pcm = vc.sink(voice.PCM(24_000, 1), buffer=2.0)
            pcm.write_nowait(b"\x10\x00" * 480 * pcm_frames)
            for packet in encoded:
                opus.write_nowait(packet)
            await asyncio.gather(opus.drain(), pcm.drain())
            passthrough = opus.passthrough
            packets = await played(fake)
            stats = vc.stats().send

            assert not passthrough
            assert pcm_frames <= stats.encoded_packets <= pcm_frames + 2
            assert stats.passthrough_packets == 0
            assert not set(encoded) & {packet.payload for packet in packets}
            assert_contiguous(packets)

    run(scenario)


def test_a_full_sink_refuses_write_nowait_and_write_waits_for_room() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            async with vc.sink(buffer=0.1) as out:
                out.write_nowait(tone(0.2))
                with pytest.raises(voice.BufferFull):
                    out.write_nowait(tone(0.02))
                await out.write(tone(1.0))
                assert out.queued < 0.2
                assert out.stats().position > 0.9

    run(scenario)


def test_a_dropped_voice_socket_resumes_without_losing_a_packet() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            states: list[tuple[State, State]] = []

            @bot.listen
            async def on_state(event: voice.StateChange) -> None:
                states.append((event.old, event.new))

            vc = await bot.voice.connect(VOICE_ID)
            async with vc.sink(buffer=2.0) as out:
                out.write_nowait(tone(1.0))
                await fake.wait_for_packets(5)
                held = vc.stats().send.held_ticks
                fake.drop_voice()
                await fake.next_voice_resume()
            packets = await played(fake)

            assert len(packets) == FRAMES_PER_SECOND + len(SILENT_TAIL)
            assert len(fake.voice_sessions) == 1
            assert vc.stats().send.resumes == 1
            assert vc.stats().send.held_ticks == held
            assert out.stats().dropped == 0
            assert_contiguous(packets)
            assert states[-2:] == [
                (State.READY, State.RESUMING),
                (State.RESUMING, State.READY),
            ]

    run(scenario)


@pytest.mark.parametrize("order", ["state_first", "server_first"])
def test_a_move_by_a_moderator_keeps_the_connection_and_its_sinks(
    order: Literal["state_first", "server_first"],
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            first_ssrc = fake.voice_sessions[-1].ssrc
            async with vc.sink() as out:
                await out.write(tone(0.2))
                moved, _ = await asyncio.gather(
                    bot.wait_for(voice.Moved),
                    fake.move_bot(OTHER_ID, order=order),
                )
                await out.write(tone(0.4))
            await vc.wait_until(State.READY)

            assert moved == voice.Moved(
                guild_id=GUILD_ID,
                old_channel_id=VOICE_ID,
                new_channel_id=OTHER_ID,
                by_us=False,
            )
            assert bot.voice.get(GUILD_ID) is vc
            assert vc.channel_id == OTHER_ID
            assert out.stats().dropped == 0
            second_ssrc = fake.voice_sessions[-1].ssrc
            assert second_ssrc != first_ssrc
            assert any(packet.ssrc == second_ssrc for packet in fake.packets)

    run(scenario)


def test_a_kick_ends_the_connection_and_closes_its_sinks() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            out = vc.sink()
            await out.write(tone(0.1))
            ended, _ = await asyncio.gather(
                bot.wait_for(
                    voice.StateChange,
                    check=lambda event: event.new is State.DISCONNECTED,
                ),
                fake.kick_bot(),
            )

            assert ended.reason is DisconnectReason.REMOVED
            assert vc.state is State.DISCONNECTED
            assert out.closed
            with pytest.raises(SinkClosed):
                await out.write(tone(0.1))
            assert bot.voice.get(GUILD_ID) is None

    run(scenario)


def test_a_4014_with_no_voice_update_after_it_ends_as_removed() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            await bot.voice.connect(VOICE_ID)
            ended, _ = await asyncio.gather(
                bot.wait_for(
                    voice.StateChange,
                    check=lambda event: event.new is State.DISCONNECTED,
                ),
                fake.close_voice(4014),
            )

            assert (ended.reason, ended.code) == (DisconnectReason.REMOVED, 4014)
            assert bot.voice.get(GUILD_ID) is None

    run(scenario)


def test_a_full_channel_times_out_naming_the_missing_event_and_leaves() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            with pytest.raises(VoiceConnectError) as raised:
                await bot.voice.connect(VOICE_ID, timeout=0.5)
            await fake.next_voice_state_update()
            leave = await fake.next_voice_state_update()

            assert raised.value.reason is DisconnectReason.TIMEOUT
            assert raised.value.code is None
            assert "VOICE_SERVER_UPDATE" in str(raised.value)
            assert leave == LEAVE
            assert bot.voice.get(GUILD_ID) is None
            assert not fake.voice_sockets

    run(scenario, full=True)


def test_a_lost_voice_server_reconnects_once_a_new_one_arrives() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            await fake.server_update(None)
            await vc.wait_until(State.RECONNECTING)
            await fake.server_update(fake.voice_endpoint)
            await vc.wait_until(State.READY)

            assert len(fake.voice_sessions) == 2
            assert bot.voice.get(GUILD_ID) is vc

    run(scenario)


def test_disconnect_leaves_closes_with_1000_and_can_be_repeated() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            ended, _ = await asyncio.gather(
                bot.wait_for(
                    voice.StateChange,
                    check=lambda event: event.new is State.DISCONNECTED,
                ),
                vc.disconnect(),
            )
            await fake.next_voice_state_update()
            leave = await fake.next_voice_state_update()
            await vc.disconnect()

            assert (ended.reason, ended.code) == (DisconnectReason.REQUESTED, None)
            assert leave == LEAVE
            assert fake.voice_sockets[-1].close_code == 1000
            assert sent(fake, 4) == [join(VOICE_ID), LEAVE]
            with pytest.raises(VoiceNotConnected):
                vc.sink()

    run(scenario)


def test_ctx_voice_joins_the_channel_of_the_user_who_ran_the_command() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, auto_defer=None)

        @bot.command(name="join")
        async def join_command(ctx: Context) -> None:
            """Join your voice channel."""
            try:
                vc = await ctx.voice.connect()
            except NotInVoice:
                await ctx.respond("not in voice")
                return
            assert ctx.voice.current is vc
            await ctx.respond(f"joined {vc.channel_id}")

        async with running(bot, fake):
            alone = await fake.interaction(2, payloads.command("join"))
            fake.voice_states[(str(GUILD_ID), MEMBER_ID)] = payloads.voice_state(
                user_id=MEMBER_ID, channel_id=str(VOICE_ID)
            )
            joined = await fake.interaction(2, payloads.command("join"))

        assert alone.json["data"]["content"] == "not in voice"
        assert joined.json["data"]["content"] == f"joined {VOICE_ID}"
        lookups = [
            request.path
            for request in fake.requests
            if request.method == "GET" and "/voice-states/" in request.path
        ]
        assert lookups == [f"/guilds/{GUILD_ID}/voice-states/{MEMBER_ID}"] * 2

    run(scenario)


def test_tracks_end_finished_when_played_out_and_stopped_when_stopped_or_skipped() -> (
    None
):
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            finished = await vc.play(voice.File(MEDIA / "tone-s16.wav"))
            assert (await finished.wait()).reason is EndReason.FINISHED
            stopped = await vc.play(voice.File(MEDIA / "tone.flac"))
            stopped.stop()
            assert (await stopped.wait()).reason is EndReason.STOPPED

            queue = voice.Queue(vc)
            skipped, following = (
                queue.add(voice.File(MEDIA / "tone.opus")) for _ in range(2)
            )
            await fake.wait_for_packets(len(fake.packets) + 5)
            queue.skip()
            assert (await skipped.wait()).reason is EndReason.STOPPED
            assert (await following.wait()).reason is EndReason.FINISHED

    run(scenario)


def test_a_queue_plays_two_files_back_to_back_without_a_gap() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            opus = await opus_packets(bot, MEDIA / "tone.opus")
            queue = voice.Queue(vc, buffer=2.0)
            tracks = [queue.add(voice.File(MEDIA / "tone.opus")) for _ in range(2)]
            await queue.join()
            packets = await played(fake)

            assert [(await track.wait()).reason for track in tracks] == [
                EndReason.FINISHED
            ] * 2
            assert [packet.payload for packet in packets] == [
                *opus,
                *opus,
                *SILENT_TAIL,
            ]
            assert_contiguous(packets)

    run(scenario)


def test_a_stage_request_patches_the_voice_state_and_a_refused_speak_leaves() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(STAGE_ID, stage="request")
            [patch] = [
                request for request in fake.requests if request.method == "PATCH"
            ]
            await vc.disconnect()
            route = f"/guilds/{GUILD_ID}/voice-states/@me"
            fake.fail("PATCH", route, 403, {"code": 50013, "message": "Missing"})
            with pytest.raises(Forbidden):
                await bot.voice.connect(STAGE_ID, stage="speak")

            assert patch.path == route
            assert patch.json["channel_id"] == str(STAGE_ID)
            assert set(patch.json) == {"channel_id", "request_to_speak_timestamp"}
            assert bot.voice.get(GUILD_ID) is None
            assert [await fake.next_voice_state_update() for _ in range(4)] == [
                join(STAGE_ID),
                LEAVE,
                join(STAGE_ID),
                LEAVE,
            ]

    run(scenario)


def test_bot_voice_defaults_apply_unless_a_call_overrides_them() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        defaults = voice.Defaults(
            self_deaf=True, self_mute=True, sink_buffer=0.5, stall="drop"
        )
        async with started(fake, voice=defaults) as bot:
            assert bot.voice.defaults is defaults
            vc = await bot.voice.connect(VOICE_ID)
            default_sink = vc.sink()
            chosen_sink = vc.sink(buffer=0.1, stall="hold")
            await vc.disconnect()
            await bot.voice.connect(VOICE_ID, self_deaf=False)

            assert (default_sink.buffer, default_sink.stall) == (0.5, "drop")
            assert (chosen_sink.buffer, chosen_sink.stall) == (0.1, "hold")
            assert sent(fake, 4) == [
                join(VOICE_ID, self_mute=True, self_deaf=True),
                LEAVE,
                join(VOICE_ID, self_mute=True),
            ]

    run(scenario)


def test_closing_the_bot_leaves_voice_before_the_gateway_closes() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            out = vc.sink()
            await out.write(tone(0.2))

        assert vc.state is State.DISCONNECTED
        assert out.closed
        assert sent(fake, 4) == [join(VOICE_ID), LEAVE]
        assert fake.voice_sockets[-1].close_code == 1000
        assert fake.connections[-1].close_code == 1000

    run(scenario)


def test_connect_external_hands_over_the_session_without_opening_voice() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            external = await bot.voice.connect_external(GUILD_ID, VOICE_ID)
            await external.disconnect()
            await fake.next_voice_state_update()
            leave = await fake.next_voice_state_update()

            assert external.state.voice_state.channel_id == VOICE_ID
            assert external.state.voice_state.user_id == int(fake.bot_user["id"])
            assert external.server.endpoint == fake.voice_endpoint
            assert external.server.token
            assert leave == LEAVE
            assert not fake.voice_sockets
            assert bot.voice.get(GUILD_ID) is None

    run(scenario)


def test_clients_and_speaking_ops_reach_listeners_and_members() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            seen: asyncio.Queue[voice.VoiceEvent] = asyncio.Queue()

            @bot.listen
            async def on_people(
                event: voice.UserJoin | voice.UserLeave | voice.SpeakingUpdate,
            ) -> None:
                seen.put_nowait(event)

            vc = await bot.voice.connect(VOICE_ID)
            await fake.clients_connect([FRIEND_ID, int(MEMBER_ID)])
            await fake.speaking(FRIEND_ID, 4321)
            await fake.client_disconnect(int(MEMBER_ID))
            events = [await seen.get() for _ in range(4)]

            assert events == [
                voice.UserJoin(guild_id=GUILD_ID, user_id=FRIEND_ID),
                voice.UserJoin(guild_id=GUILD_ID, user_id=int(MEMBER_ID)),
                voice.SpeakingUpdate(
                    guild_id=GUILD_ID,
                    user_id=FRIEND_ID,
                    ssrc=4321,
                    flags=SpeakingFlags.MICROPHONE,
                ),
                voice.UserLeave(guild_id=GUILD_ID, user_id=int(MEMBER_ID)),
            ]
            assert vc.members == {FRIEND_ID}

    run(scenario)


def test_a_dropping_sink_loses_the_audio_of_a_stall_and_a_holding_one_keeps_it() -> (
    None
):
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            holding = vc.sink(buffer=2.0, stall="hold")
            dropping = vc.sink(buffer=2.0, stall="drop")
            holding.write_nowait(tone(1.0))
            dropping.write_nowait(tone(1.0))
            state = fake.voice_states[(str(GUILD_ID), fake.bot_user["id"])]
            state["channel_id"] = str(OTHER_ID)
            await asyncio.gather(
                bot.wait_for(voice.Moved),
                fake.dispatch("VOICE_STATE_UPDATE", state),
            )
            stalled = await bot.wait_for(voice.MediaStalled)
            resumed, _ = await asyncio.gather(
                bot.wait_for(voice.MediaResumed),
                fake.server_update(fake.voice_endpoint),
            )

            assert await holding.drain()
            assert not await dropping.drain()
            assert holding.stats().dropped == 0
            assert dropping.stats().dropped > 0
            assert stalled.direction is resumed.direction is voice.Direction.SEND
            assert resumed.duration >= stalled.duration > 0

    run(scenario)


def test_versions_and_diagnostics_describe_the_build_and_never_the_token() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            diagnostics = repr(vc.diagnostics())

            assert set(voice.versions()) == {
                "bostie",
                "libopus",
                "davey",
                "dave_protocol",
            }
            assert fake.voice_sessions[-1].identify["token"] not in diagnostics
            assert fake.token not in diagnostics
            assert str(GUILD_ID) in diagnostics

    run(scenario)


def test_voice_listeners_and_waiters_stay_out_of_the_gateway_filter(
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
        changes: list[voice.StateChange] = []

        @bot.listen
        async def on_state(event: voice.StateChange) -> None:
            changes.append(event)

        async with running(bot, fake):
            moved = asyncio.ensure_future(bot.wait_for(voice.Moved))
            await asyncio.sleep(0)
            await bot.voice.connect(VOICE_ID)
            moved.cancel()

        assert {name for names in filters if names for name in names} == {
            "RATE_LIMITED"
        }
        assert [(change.old, change.new) for change in changes] == [
            (State.CONNECTING, State.READY),
            (State.READY, State.DISCONNECTED),
        ]
        assert changes[-1].reason is DisconnectReason.REQUESTED

    run(scenario)


def test_voice_without_the_voice_states_intent_raises_before_joining() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake, intents=Intents.GUILDS) as bot:
            with pytest.raises(RuntimeError, match="GUILD_VOICE_STATES"):
                await bot.voice.connect(VOICE_ID)
            assert sent(fake, 4) == []

    run(scenario)


def test_a_suppressed_stage_warns_once_when_audio_starts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        fake.suppress = True
        async with started(fake) as bot:
            vc = await bot.voice.connect(STAGE_ID)
            assert vc.suppress
            with caplog.at_level(logging.WARNING, logger="bostie.voice"):
                for _ in range(2):
                    async with vc.sink() as out:
                        await out.write(tone(0.04))

        warnings = [
            record for record in caplog.records if "suppressed" in record.message
        ]
        assert len(warnings) == 1

    run(scenario)


def test_a_busy_python_thread_never_starves_the_clock() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            stop = threading.Event()

            def spin() -> None:
                while not stop.is_set():
                    pass

            busy = threading.Thread(target=spin)
            busy.start()
            try:
                async with vc.sink(buffer=1.0) as out:
                    await out.write(tone(5.0))
                    underruns = out.stats().underruns
            finally:
                stop.set()
                busy.join()
            packets = await played(fake)

            assert underruns == 0
            assert vc.stats().send.encoded_packets == 5 * FRAMES_PER_SECOND
            assert_contiguous(packets)

    run(scenario)
