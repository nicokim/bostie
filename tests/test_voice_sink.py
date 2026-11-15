import array
import asyncio
import contextlib
import hashlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from bostie import Bot, voice
from bostie.errors import BufferFull, SinkClosed
from bostie.testing import FakeDiscord, Packet, payloads, run, running, settle
from bostie.voice.sink import _piece_bytes

GUILD_ID = int(payloads.voice_channel()["guild_id"])
VOICE_ID = int(payloads.voice_channel()["id"])
ELSEWHERE_ID = VOICE_ID + 2
SECOND = 48_000 * 2 * 2
SAMPLE = b"\x00\x00\x00\x00"
SILENCE = b"\xf8\xff\xfe"
SILENT_TAIL = [SILENCE] * 5
NOISE = hashlib.shake_256(b"noise").digest(SECOND)


@contextlib.asynccontextmanager
async def started(fake: FakeDiscord, **options: Any) -> AsyncIterator[Bot]:
    bot = Bot(api_url=fake.api_url, **options)
    async with running(bot, fake):
        yield bot


@contextlib.asynccontextmanager
async def connected(fake: FakeDiscord) -> AsyncIterator[voice.VoiceConnection]:
    async with started(fake) as bot:
        yield await bot.voice.connect(VOICE_ID)


async def played(fake: FakeDiscord) -> list[Packet]:
    count = len(SILENT_TAIL)
    while True:
        packets = await fake.voice.wait_for_packets(count)
        if [packet.payload for packet in packets[-len(SILENT_TAIL) :]] == SILENT_TAIL:
            return packets
        count += 1


async def reached(marker: voice.Marker) -> bool:
    return await marker


def test_sinks_take_buffer_and_stall_from_the_defaults_unless_given() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        defaults = voice.Defaults(sink_buffer=0.4, stall="drop")
        async with started(fake, voice=defaults) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            sinks = [
                vc.sink(),
                vc.sink(
                    voice.PCM(24_000, 1, "f32le"), buffer=0.5, volume=0.3, stall="hold"
                ),
                vc.sink(voice.Opus()),
            ]

            assert [
                (out.format, out.buffer, out.stall, out.volume) for out in sinks
            ] == [
                (voice.PCM(), 0.4, "drop", 1.0),
                (voice.PCM(24_000, 1, "f32le"), 0.5, "hold", 0.3),
                (voice.Opus(), 0.4, "drop", 1.0),
            ]

    run(scenario, voice=True)


def test_write_waits_while_full_and_resumes_as_the_sink_plays() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(buffer=0.2)
            writing = asyncio.create_task(out.write(bytes(int(SECOND * 0.3))))
            await settle()
            waited = not writing.done()
            await writing

            assert waited
            assert out.queued <= 0.2

    run(scenario, voice=True)


def test_one_wake_resumes_every_waiter_even_if_another_was_cancelled() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(buffer=0.1)
            out.write_nowait(bytes(SECOND // 5))
            first = asyncio.create_task(out.write(SAMPLE))
            second = asyncio.create_task(out.write(SAMPLE))
            cancelled = asyncio.create_task(out.write(SAMPLE))
            await settle()
            waited = not (first.done() or second.done())
            cancelled.cancel()
            await asyncio.gather(first, second)

            assert waited
            assert cancelled.cancelled()

    run(scenario, voice=True)


@pytest.mark.parametrize(
    ("format", "buffer", "piece"),
    [
        (voice.PCM(44_100, 1), 0.2, 2205 * 2),
        (voice.PCM(), 0.04, 960 * 4),
        (voice.PCM(44_100, 1, "f32le"), 0.2, 2205 * 4),
        (voice.Opus(), 0.2, 0),
    ],
)
def test_pieces_are_20_ms_or_a_quarter_buffer_in_whole_sample_frames(
    format: voice.Format, buffer: float, piece: int
) -> None:
    assert _piece_bytes(format, buffer) == piece


def test_an_opus_write_reaches_discord_as_one_packet() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        packet = voice.OpusEncoder().encode(NOISE[:3840])[0]
        async with connected(fake) as vc:
            async with vc.sink(voice.Opus(), buffer=0.02) as out:
                await out.write(packet)
            packets = await played(fake)

        assert [p.payload for p in packets] == [packet, *SILENT_TAIL]

    run(scenario, voice=True)


def test_any_c_contiguous_buffer_is_accepted_without_tobytes() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(buffer=1.0)
            samples = array.array("h", [1, -1, 2, -2])
            data = bytes(samples)
            await out.write(data)
            await out.write(bytearray(data))
            await out.write(memoryview(b"xx" + data + b"yy")[2:-2])
            await out.write(samples)
            out.write_nowait(samples)

            assert await out.drain()

    run(scenario, voice=True)


def test_write_nowait_writes_the_whole_chunk_or_raises_buffer_full() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(buffer=0.2)
            out.write_nowait(bytes(SECOND))

            assert out.queued > 0.5
            with pytest.raises(BufferFull):
                out.write_nowait(SAMPLE)

    run(scenario, voice=True)


def test_a_closed_sink_raises_sink_closed_and_ignores_the_rest() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink()
            out.close()
            with pytest.raises(SinkClosed):
                await out.write(SAMPLE)
            with pytest.raises(SinkClosed):
                out.write_nowait(b"")
            with pytest.raises(SinkClosed):
                await out.mark()
            out.clear()
            out.volume = 0.5
            out.close()

            assert await out.drain() is False
            assert (out.closed, out.queued, out.passthrough) == (True, 0.0, False)

    run(scenario, voice=True)


def test_ending_the_connection_closes_its_sinks_and_frees_their_waiters() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(buffer=0.2)
            out.write_nowait(bytes(SECOND))
            marker = out.mark()
            blocked = asyncio.create_task(out.write(bytes(SECOND)))
            await settle()
            await fake.kick_bot()

            with pytest.raises(SinkClosed):
                await blocked
            assert await marker is False
            assert out.closed

    run(scenario, voice=True)


def test_after_must_be_an_open_sink_of_this_connection() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        fake.channels[str(ELSEWHERE_ID)] = payloads.voice_channel(
            id=str(ELSEWHERE_ID), guild_id=str(GUILD_ID + 1)
        )
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            other = await bot.voice.connect(ELSEWHERE_ID)
            first = vc.sink()
            first.close()

            assert not vc.sink(after=first).closed
            with pytest.raises(ValueError, match="this connection"):
                other.sink(after=vc.sink())

    run(scenario, voice=True)


def test_vc_sinks_lists_the_open_sinks_and_stop_closes_them_all() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            first, second = vc.sink(), vc.sink(voice.Opus())
            listed = vc.sinks
            vc.stop()

            assert listed == (first, second)
            assert (first.closed, second.closed, vc.sinks) == (True, True, ())

    run(scenario, voice=True)


def test_drain_returns_whether_everything_played() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink()
            await out.write(bytes(SECOND // 10))
            played_out = await out.drain()
            await out.write(bytes(SECOND // 10))
            draining = asyncio.create_task(out.drain())
            await settle()
            out.clear()

            assert (played_out, await draining) == (True, False)

    run(scenario, voice=True)


def test_markers_report_seconds_and_survive_a_cancelled_wait() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(buffer=1.0)
            await out.write(bytes(SECOND // 5))
            marker = out.mark()
            first = asyncio.create_task(reached(marker))
            second = asyncio.create_task(reached(marker))
            await settle()
            first.cancel()
            await settle()
            pending = not marker.done()

            assert marker.position == 0.2
            assert pending
            assert await second is True
            assert marker.done()
            assert await marker is True

    run(scenario, voice=True)


def test_clear_drops_the_audio_before_until_or_after_since() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(buffer=2.0)
            tenth = bytes(SECOND // 10)
            out.write_nowait(tenth)
            first = out.mark()
            out.write_nowait(tenth)
            second = out.mark()
            out.clear(since=first)
            kept = (await first, await second)
            out.write_nowait(tenth)
            third = out.mark()
            out.write_nowait(tenth)
            fourth = out.mark()
            out.clear(until=third)

            assert kept == (True, False)
            assert (await third, await fourth) == (False, True)
            with pytest.raises(ValueError, match="not both"):
                out.clear(until=third, since=third)

    run(scenario, voice=True)


def test_async_with_drains_then_closes_on_a_normal_exit() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            async with vc.sink() as out:
                await out.write(bytes(SECOND // 10))
            packets = await played(fake)

            assert out.closed
            assert len(packets) == 5 + len(SILENT_TAIL)

    run(scenario, voice=True)


@pytest.mark.parametrize("ending", [ValueError, asyncio.CancelledError])
def test_async_with_discards_the_audio_when_the_block_raises(
    ending: type[BaseException],
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(buffer=2.0)

            async def write_then_end() -> None:
                async with out:
                    out.write_nowait(bytes(SECOND))
                    await fake.voice.wait_for_packets(1)
                    raise ending

            with pytest.raises(ending):
                await write_then_end()
            packets = await played(fake)

            assert out.closed
            assert len(packets) < 25

    run(scenario, voice=True)


def test_volume_and_filters_are_kept_on_the_sink() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(volume=0.8)
            opened = (out.volume, out.filters)
            nightcore = voice.Filters(
                equalizer={60: 3.0}, bass=2.0, speed=1.25, pitch=1.25
            )
            out.volume = 0.25
            out.filters = nightcore
            slow = vc.sink(filters=voice.Filters(speed=0.5))

            assert opened == (0.8, voice.Filters())
            assert (out.volume, out.filters) == (0.25, nightcore)
            assert slow.filters == voice.Filters(speed=0.5)

    run(scenario, voice=True)


def test_stats_keep_their_last_value_after_close() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with connected(fake) as vc:
            out = vc.sink(buffer=1.0)
            await out.write(bytes(SECOND // 10))
            await out.drain()
            stats = out.stats()
            out.close()

            assert (stats.sent, stats.position) == (0.1, 0.1)
            assert out.stats() == stats

    run(scenario, voice=True)


def test_an_encoder_without_a_bitrate_encodes_at_64_kbps() -> None:
    default = voice.OpusEncoder().encode(NOISE)
    chosen = voice.OpusEncoder(encoder=voice.EncoderSettings(bitrate=24_000)).encode(
        NOISE
    )

    assert len(default) == len(chosen) == 50
    assert sum(map(len, default)) * 8 == pytest.approx(64_000, rel=0.15)
    assert sum(map(len, chosen)) * 8 == pytest.approx(24_000, rel=0.15)
