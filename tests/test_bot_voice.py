import asyncio
import contextlib
import itertools
import math
import struct
import threading
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable
from pathlib import Path
from typing import Any

import pytest

from bostie import Bot, Context, ShardNotRunning, voice
from bostie.enums import SpeakingFlags
from bostie.gateway import Shards
from bostie.testing import FakeDiscord, Packet, payloads, running
from bostie.testing.payloads import Json
from bostie.voice import DisconnectReason, EndReason, NotInVoice, SinkClosed, State

type Scenario = Callable[[FakeDiscord], Coroutine[Any, Any, None]]
type Heard = voice.Frame | voice.SpeechStart | voice.SpeechStop | voice.UserLeave

MEDIA = Path(__file__).parents[1] / "crates/bostie-core/src/voice/testdata/media"
GUILD_ID = int(payloads.voice_channel()["guild_id"])
VOICE_ID = int(payloads.voice_channel()["id"])
OTHER_ID = VOICE_ID + 1
MEMBER_ID = "613374681415811073"
FRIEND_ID = 1_100_000_000_000_000_001
SILENCE = b"\xf8\xff\xfe"
UNOPENABLE = b"\x80\x78" + struct.pack(">HII", 0, 0, 4321) + bytes(32)
SILENT_TAIL = [SILENCE] * 5
FRAMES_PER_SECOND = 50
LOST_FROM, LOST_UNTIL = 20, 22
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


def test_connect_uses_the_bot_defaults_and_closing_the_bot_leaves_first() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake, voice=voice.Defaults(self_deaf=True)) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            identify = await fake.next_voice_identify()

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

        assert bot.voice.get(GUILD_ID) is None
        assert sent(fake, 4) == [join(VOICE_ID, self_deaf=True), LEAVE]
        assert fake.voice_sockets[-1].close_code == 1000
        assert fake.connections[-1].close_code == 1000

    run(scenario)


def test_a_connect_the_core_refuses_raises_and_registers_nothing() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        other_shard = 1 - (GUILD_ID >> 22) % 2
        async with started(fake, shard_count=2, shard_ids=[other_shard]) as bot:
            with pytest.raises(ShardNotRunning):
                await bot.voice.connect(VOICE_ID)

            assert bot.voice.get(GUILD_ID) is None

    run(scenario)


def test_a_second_of_pcm_plays_as_fifty_opus_packets_and_five_silent_ones() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            async with vc.sink(buffer=2.0) as out:
                out.write_nowait(tone(1.0))
                await fake.wait_for_packets(1)
                speaking = voice_ops(fake, 5)
                await out.drain()
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
            assert out.stats().underruns == 0

    run(scenario)


def test_an_opus_sinks_stats_reach_python_field_by_field() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            packets = voice.OpusEncoder().encode(tone(0.2))
            async with vc.sink(voice.Opus(), buffer=1.0) as out:
                for packet in packets:
                    out.write_nowait(packet)
                await played(fake)
                stats = out.stats()

            assert stats == voice.SinkStats(0.2, 0.2, 1, 0, 0.0, len(packets))

    run(scenario)


def test_receive_a_spoken_second_from_the_fake() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            encoder = voice.OpusEncoder()
            spoken = encoder.encode(tone(1.0)) + encoder.flush()

            async def listen() -> tuple[list[Heard], voice.ReceiverStats]:
                heard: list[Heard] = []
                async with vc.receive(voice.PCM(16_000, 1), markers=True) as rx:
                    await fake.speaking(FRIEND_ID, 4321, 1)
                    await fake.send_audio(4321, spoken[:LOST_FROM])
                    await fake.send_audio(
                        4321,
                        spoken[LOST_UNTIL:],
                        sequence=LOST_UNTIL,
                        timestamp=LOST_UNTIL * SAMPLES_PER_FRAME,
                    )
                    async for item in rx:
                        heard.append(item)
                        if isinstance(item, voice.SpeechStop):
                            break
                    assert await rx.mark()
                    return heard, rx.stats()

            start, stop, (heard, stats) = await asyncio.gather(
                bot.wait_for(voice.SpeechStart),
                bot.wait_for(voice.SpeechStop),
                listen(),
            )

            frames = [item for item in heard if isinstance(item, voice.Frame)]
            assert [type(item) for item in heard] == [
                voice.SpeechStart,
                *[voice.Frame] * FRAMES_PER_SECOND,
                voice.SpeechStop,
            ]
            assert all(
                (frame.user_id, frame.ssrc, len(frame.data)) == (FRIEND_ID, 4321, 640)
                for frame in frames
            )
            lost = LOST_UNTIL - LOST_FROM
            assert [index for index, frame in enumerate(frames) if frame.lost] == list(
                range(LOST_FROM, LOST_UNTIL)
            )
            assert stats == voice.ReceiverStats(FRAMES_PER_SECOND, 0, lost)
            assert all(
                after.offset - before.offset == SAMPLES_PER_FRAME
                for before, after in itertools.pairwise(frames)
            )
            assert (start.user_id, start.ssrc, start.offset) == (FRIEND_ID, 4321, None)
            assert (stop.user_id, stop.ssrc, stop.offset) == (FRIEND_ID, 4321, None)
            assert vc.speaking == {}

    run(scenario)


def test_a_move_by_a_moderator_keeps_the_connection_and_its_sinks() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            first_ssrc = fake.voice_sessions[-1].ssrc
            async with vc.sink() as out:
                await out.write(tone(0.2))
                moved, _ = await asyncio.gather(
                    bot.wait_for(voice.Moved), fake.move_bot(OTHER_ID)
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


def test_voice_ops_and_the_bot_voice_flags_reach_python_field_by_field() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        fake.mute = True
        async with started(fake) as bot:
            seen: asyncio.Queue[voice.VoiceEvent] = asyncio.Queue()

            @bot.listen
            async def on_people(
                event: voice.UserJoin | voice.UserLeave | voice.SpeakingUpdate,
            ) -> None:
                seen.put_nowait(event)

            vc = await bot.voice.connect(VOICE_ID, self_mute=True)
            joined = (vc.self_mute, vc.self_deaf, vc.mute, vc.deaf, vc.suppress)
            await fake.clients_connect([FRIEND_ID, int(MEMBER_ID)])
            await fake.speaking(FRIEND_ID, 4321, 5)
            await fake.client_disconnect(int(MEMBER_ID))
            events = [await seen.get() for _ in range(4)]
            members = vc.members
            fake.mute, fake.deaf, fake.suppress = False, True, True
            await asyncio.gather(bot.wait_for(voice.Moved), fake.move_bot(OTHER_ID))

            assert events == [
                voice.UserJoin(guild_id=GUILD_ID, user_id=FRIEND_ID),
                voice.UserJoin(guild_id=GUILD_ID, user_id=int(MEMBER_ID)),
                voice.SpeakingUpdate(
                    guild_id=GUILD_ID,
                    user_id=FRIEND_ID,
                    ssrc=4321,
                    flags=SpeakingFlags.MICROPHONE | SpeakingFlags.PRIORITY,
                ),
                voice.UserLeave(guild_id=GUILD_ID, user_id=int(MEMBER_ID)),
            ]
            assert members == {FRIEND_ID}
            assert joined == (True, False, True, False, False)
            assert (vc.self_mute, vc.self_deaf, vc.mute, vc.deaf, vc.suppress) == (
                True,
                False,
                False,
                True,
                True,
            )

    run(scenario)


def test_rtcp_reports_reach_listeners_with_the_users_of_their_ssrcs() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            assert bot.user is not None
            own = fake.voice_sessions[-1].ssrc
            await asyncio.gather(
                bot.wait_for(voice.SpeakingUpdate), fake.speaking(FRIEND_ID, 4321, 1)
            )
            block = struct.pack(">6I", own, 0x40FF_FFFE, 70_000, 480, 7, 32_768)
            report = struct.pack(">BBHI", 0x81, 201, 7, 4321) + block
            received, _ = await asyncio.gather(
                bot.wait_for(voice.RtcpPacket), fake.send_rtcp(report)
            )

            assert received == voice.RtcpPacket(
                guild_id=vc.guild_id,
                packet_type=201,
                count=1,
                ssrc=4321,
                user_id=FRIEND_ID,
                reports=(
                    voice.ReceptionReport(
                        own, bot.user.id, 0.25, -2, 70_000, 0.01, 7, 0.5
                    ),
                ),
                data=block,
            )

    run(scenario)


def test_a_receive_stall_travels_from_the_core_to_listeners_and_ends() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, voice=voice.Defaults(stall_after=0.05))
        changes: asyncio.Queue[voice.MediaStalled | voice.MediaResumed] = (
            asyncio.Queue()
        )

        @bot.listen
        async def media(event: voice.MediaStalled | voice.MediaResumed) -> None:
            changes.put_nowait(event)

        async with running(bot, fake):
            vc = await bot.voice.connect(VOICE_ID)
            spoken = voice.OpusEncoder().encode(tone(0.2))
            async with vc.receive(voice.PCM(16_000, 1)):
                await fake.speaking(FRIEND_ID, 4321, 1)
                await fake.send_datagram(UNOPENABLE)
                stalled = await changes.get()
                await fake.send_audio(4321, spoken)
                resumed = await changes.get()

        assert vc.stall_after == 0.05
        assert isinstance(stalled, voice.MediaStalled)
        assert isinstance(resumed, voice.MediaResumed)
        assert (stalled.guild_id, stalled.direction) == (
            GUILD_ID,
            voice.Direction.RECEIVE,
        )
        assert resumed.direction is voice.Direction.RECEIVE
        assert 0.05 <= stalled.duration <= resumed.duration

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


def test_voice_listeners_and_waiters_stay_out_of_the_gateway_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filters: list[list[str] | None] = []
    send_filter = Shards.filter

    def record(gateway: Shards, names: Iterable[str] | None) -> None:
        filters.append(None if names is None else list(names))
        send_filter(gateway, names)

    monkeypatch.setattr(Shards, "filter", record)

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
            "GUILD_CREATE",
            "GUILD_DELETE",
            "RATE_LIMITED",
        }
        assert [(change.old, change.new) for change in changes] == [
            (State.CONNECTING, State.READY),
            (State.READY, State.DISCONNECTED),
        ]
        assert changes[-1].reason is DisconnectReason.REQUESTED

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
            assert len(packets) == 5 * FRAMES_PER_SECOND + len(SILENT_TAIL)
            assert_contiguous(packets)

    run(scenario)
