import array
import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any

import pytest
from voice_fakes import FakeBot, FakeSink, connected, disconnected, run, settle

from bostie import voice
from bostie.errors import BufferFull, SinkClosed
from bostie.voice import encoder as encoder_module
from bostie.voice.sink import Sink

SECOND = 48_000 * 2 * 2


async def open_sink(bot: FakeBot, *args: Any, **options: Any) -> tuple[Sink, FakeSink]:
    vc = await connected(bot)
    sink = vc.sink(*args, **options)
    return sink, bot.core.sinks[-1]


async def consume(native: FakeSink, step: float = 0.02) -> None:
    while True:
        await asyncio.sleep(0)
        native.pull(step)


def test_sinks_take_buffer_and_stall_from_the_defaults_unless_given() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        vc.sink()
        vc.sink(voice.PCM(24_000, 1, "f32le"), buffer=0.5, volume=0.3, stall="hold")
        vc.sink(voice.Opus())
        assert [call[2] for call in bot.calls("voice_sink")] == [
            {
                "kind": "pcm",
                "rate": 48_000,
                "channels": 2,
                "sample": "s16le",
                "buffer": 0.4,
                "volume": 1.0,
                "stall": "drop",
            },
            {
                "kind": "pcm",
                "rate": 24_000,
                "channels": 1,
                "sample": "f32le",
                "buffer": 0.5,
                "volume": 0.3,
                "stall": "hold",
            },
            {
                "kind": "opus",
                "rate": 48_000,
                "channels": 2,
                "sample": "s16le",
                "buffer": 0.4,
                "volume": 1.0,
                "stall": "drop",
            },
        ]

    run(scenario, defaults=voice.Defaults(sink_buffer=0.4, stall="drop"))


def test_write_waits_while_full_and_resumes_on_the_wake() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot, buffer=0.2)
        writing = asyncio.ensure_future(sink.write(bytes(int(SECOND * 0.3))))
        await settle()
        assert not writing.done()
        assert native.written == pytest.approx(0.2)
        native.pull(0.1)
        await writing
        assert native.written == pytest.approx(0.3)

    run(scenario)


def test_one_wake_resumes_every_waiter_even_if_another_was_cancelled() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot, buffer=0.2)
        await sink.write(bytes(int(SECOND * 0.2)))
        first = asyncio.ensure_future(sink.write(b"\x01\x00\x01\x00"))
        second = asyncio.ensure_future(sink.write(b"\x02\x00\x02\x00"))
        cancelled = asyncio.ensure_future(sink.write(b"\x03\x00\x03\x00"))
        await settle()
        cancelled.cancel()
        native.pull(0.1)
        await asyncio.gather(first, second)
        assert native.writes[-2:] == [b"\x01\x00\x01\x00", b"\x02\x00\x02\x00"]
        assert cancelled.cancelled()

    run(scenario)


@pytest.mark.parametrize(
    ("format", "buffer", "size", "pieces"),
    [
        (voice.PCM(44_100, 1), 0.2, 10_000, [4410, 4410, 1180]),
        (voice.PCM(), 0.04, 5000, [3840, 1160]),
        (voice.PCM(44_100, 1, "f32le"), 0.2, 20_000, [8820, 8820, 2360]),
    ],
)
def test_pieces_are_20_ms_or_a_quarter_buffer_in_whole_sample_frames(
    format: voice.PCM, buffer: float, size: int, pieces: list[int]
) -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot, format, buffer=buffer)
        await sink.write(bytes(size))
        assert [len(write) for write in native.writes] == pieces

    run(scenario)


def test_opus_writes_go_to_the_native_sink_whole() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot, voice.Opus(), buffer=0.02)
        packet = bytes(range(256)) * 20
        await sink.write(packet)
        assert native.writes == [packet]

    run(scenario)


def test_any_c_contiguous_buffer_is_accepted_without_tobytes() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot)
        samples = array.array("h", [1, -1, 2, -2])
        data = bytes(samples)
        await sink.write(data)
        await sink.write(bytearray(data))
        await sink.write(memoryview(b"xx" + data + b"yy")[2:-2])
        await sink.write(samples)
        sink.write_nowait(samples)
        assert native.writes == [data] * 5

    run(scenario)


def test_write_nowait_writes_the_whole_chunk_or_raises_buffer_full() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot, buffer=0.2)
        sink.write_nowait(bytes(SECOND))
        assert [len(write) for write in native.writes] == [SECOND]
        with pytest.raises(BufferFull):
            sink.write_nowait(b"\x00\x00\x00\x00")

    run(scenario)


def test_a_closed_sink_raises_sink_closed_without_a_native_call() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot)
        sink.close()
        native.failure = AssertionError("native call on a closed sink")
        with pytest.raises(SinkClosed):
            await sink.write(b"\x00\x00\x00\x00")
        with pytest.raises(SinkClosed):
            sink.write_nowait(b"")
        with pytest.raises(SinkClosed):
            sink.mark()
        sink.clear()
        sink.volume = 0.5
        sink.close()
        assert await sink.drain() is False
        assert (sink.closed, sink.queued, sink.passthrough) == (True, 0.0, False)

    run(scenario)


def test_a_native_runtime_error_is_left_as_it_is() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot)
        native.failure = RuntimeError("the process forked")
        with pytest.raises(RuntimeError, match="forked"):
            await sink.write(b"\x00\x00\x00\x00")
        assert not sink.closed

    run(scenario)


def test_ending_the_connection_closes_its_sinks_and_frees_their_waiters() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, _ = await open_sink(bot, buffer=0.2)
        await sink.write(bytes(int(SECOND * 0.2)))
        marker = sink.mark()
        blocked = asyncio.ensure_future(sink.write(bytes(SECOND)))
        await settle()
        bot.deliver(disconnected("removed"))
        with pytest.raises(SinkClosed):
            await blocked
        assert await marker is False
        assert sink.closed
        assert bot.voice._handles == {}

    run(scenario)


def test_close_succeeds_when_the_core_already_closed_the_sink() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot)
        native.close()
        sink.close()
        assert sink.closed
        assert bot.voice._handles == {}

    run(scenario)


def test_drain_flushes_and_returns_whether_everything_played() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot)
        await sink.write(bytes(SECOND // 10))
        draining = asyncio.ensure_future(sink.drain())
        await settle()
        assert native.flushes == 1
        native.pull(0.1)
        assert await draining is True
        await sink.write(bytes(SECOND // 10))
        draining = asyncio.ensure_future(sink.drain())
        await settle()
        sink.clear()
        assert await draining is False

    run(scenario)


def test_markers_report_seconds_and_survive_a_cancelled_wait() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot, buffer=1.0)
        await sink.write(bytes(SECOND // 4))
        marker = sink.mark()
        assert marker.position == pytest.approx(0.25)
        first = asyncio.ensure_future(marker)
        second = asyncio.ensure_future(marker)
        await settle()
        first.cancel()
        await settle()
        assert not marker.done()
        native.pull(0.25)
        assert await second is True
        assert marker.done()
        assert await marker is True

    run(scenario)


def test_clear_until_a_marker_passes_its_id() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot)
        await sink.write(bytes(SECOND // 10))
        marker = sink.mark()
        sink.clear(until=marker)
        sink.clear()
        assert native.clears == [marker.id, None]

    run(scenario)


def test_async_with_drains_then_closes_on_a_normal_exit() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        consumer: asyncio.Future[None] | None = None
        async with vc.sink() as out:
            native = bot.core.sinks[-1]
            await out.write(bytes(SECOND // 10))
            consumer = asyncio.ensure_future(consume(native))
        consumer.cancel()
        assert (native.flushes, native.clears, native.closed) == (1, [], True)
        assert out.closed

    run(scenario)


def test_async_with_clears_then_closes_on_an_error() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        with pytest.raises(ValueError, match="boom"):
            async with vc.sink() as out:
                await out.write(bytes(SECOND // 10))
                raise ValueError("boom")
        native = bot.core.sinks[-1]
        assert (native.flushes, native.clears, native.closed) == (0, [None], True)

    run(scenario)


def test_async_with_clears_then_closes_when_the_producer_is_cancelled() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)

        async def produce() -> None:
            async with vc.sink(buffer=0.2) as out:
                await out.write(bytes(SECOND * 5))

        producer = asyncio.ensure_future(produce())
        await settle()
        producer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer
        native = bot.core.sinks[-1]
        assert (native.flushes, native.clears, native.closed) == (0, [None], True)

    run(scenario)


def test_volume_goes_to_the_native_sink() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot, volume=0.8)
        assert sink.volume == 0.8
        sink.volume = 0.25
        assert (sink.volume, native.volume) == (0.25, 0.25)

    run(scenario)


def test_stats_are_in_seconds_and_keep_their_last_value_after_close() -> None:
    async def scenario(bot: FakeBot) -> None:
        sink, native = await open_sink(bot, buffer=1.0)
        await sink.write(bytes(SECOND // 2))
        native.pull(0.5)
        native.counters = (24_000, 2, 3, 4800, 7)
        expected = voice.SinkStats(0.5, 0.5, 2, 3, 0.1, 7)
        assert sink.stats() == expected
        sink.close()
        assert sink.stats() == expected

    run(scenario)


def test_opus_encoder_fills_in_64_kbps_and_whole_percents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, Any]] = []

    class NativeEncoder:
        def __init__(self, **options: Any) -> None:
            created.append(options)

    monkeypatch.setattr(
        encoder_module, "_native", SimpleNamespace(OpusEncoder=NativeEncoder)
    )
    voice.OpusEncoder(voice.PCM(24_000, 1))
    voice.OpusEncoder(
        encoder=voice.EncoderSettings(
            bitrate=96_000,
            application="voip",
            fec=False,
            packet_loss=0.29,
            complexity=0,
        )
    )
    assert created == [
        {
            "rate": 24_000,
            "channels": 1,
            "sample": "s16le",
            "bitrate": 64_000,
            "application": "audio",
            "fec": True,
            "packet_loss": 10,
            "complexity": 10,
        },
        {
            "rate": 48_000,
            "channels": 2,
            "sample": "s16le",
            "bitrate": 96_000,
            "application": "voip",
            "fec": False,
            "packet_loss": 29,
            "complexity": 0,
        },
    ]
