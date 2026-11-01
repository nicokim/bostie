import asyncio
import time
from collections import deque
from collections.abc import Buffer, Callable, Coroutine, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Self, cast

import pytest

from bostie.enums import ClosedReason, State
from bostie.errors import (
    Empty,
    ReceiverClosed,
    SinkClosed,
    SourceError,
    VoiceNotConnected,
)
from bostie.voice import layers
from bostie.voice.events import SpeechStart, SpeechStop
from bostie.voice.formats import (
    DEFAULT_PCM,
    PCM,
    EncoderSettings,
    Format,
    Opus,
    ReceiveFormat,
    SinkStall,
)
from bostie.voice.frames import Frame
from bostie.voice.layers import (
    EndReason,
    Queue,
    Recorder,
    Track,
    Utterance,
    utterances,
)

FRAME = 0.02
SETTLE = 20

type Scenario = Callable[["Clock"], Coroutine[Any, Any, None]]


def frames(name: str, stop: int, start: int = 0) -> list[bytes]:
    return [f"{name}{index}".encode() for index in range(start, stop)]


class Clock:
    def __init__(self) -> None:
        self.ticks = 0
        self.sinks: list[FakeSink] = []
        self.sleepers: list[tuple[int, asyncio.Future[None]]] = []

    @property
    def now(self) -> float:
        return self.ticks * FRAME

    async def sleep(self, seconds: float) -> None:
        future = asyncio.get_running_loop().create_future()
        self.sleepers.append((self.ticks + round(seconds / FRAME), future))
        await future

    async def until(self, ready: Callable[[], bool]) -> None:
        while not ready():
            await self.sleep(FRAME)

    def tick(self) -> None:
        self.ticks += 1
        for sink in tuple(self.sinks):
            sink.pull()
        due = [future for at, future in self.sleepers if at <= self.ticks]
        self.sleepers = [entry for entry in self.sleepers if entry[0] > self.ticks]
        for future in due:
            if not future.done():
                future.set_result(None)

    async def run(self) -> None:
        while True:
            for _ in range(SETTLE):
                await asyncio.sleep(0)
            self.tick()


class FakeMarker:
    def __init__(self, id: int, index: int, future: asyncio.Future[bool]) -> None:
        self.id = id
        self.index = index
        self.position = index * FRAME
        self.future = future
        self.played = True

    def done(self) -> bool:
        return self.future.done()

    def __await__(self) -> Generator[Any, None, bool]:
        return asyncio.shield(self.future).__await__()


@dataclass(frozen=True)
class FakeStats:
    position: float


class FakeSink:
    def __init__(
        self,
        vc: "FakeConnection",
        format: Format,
        buffer: float,
        volume: float,
        stall: str,
    ) -> None:
        self.vc = vc
        self.format = format
        self.buffer = buffer
        self.volume = volume
        self.stall = stall
        self.capacity = round(buffer / FRAME)
        self.queue: deque[tuple[int, bytes]] = deque()
        self.written = 0
        self.playhead = 0
        self.markers: list[FakeMarker] = []
        self.marks = 0
        self.closed = False
        self.room: asyncio.Future[None] | None = None
        self.sent: list[bytes] = []
        self.ticks: list[bytes | None] = []
        self.clears: list[int | None] = []
        self.drains = 0

    @property
    def passthrough(self) -> bool:
        return isinstance(self.format, Opus)

    async def write(self, data: Buffer) -> None:
        while True:
            if self.closed:
                raise SinkClosed("the sink is closed")
            if len(self.queue) < self.capacity:
                break
            if self.room is None:
                self.room = asyncio.get_running_loop().create_future()
            await asyncio.shield(self.room)
        self.queue.append((self.written, bytes(data)))
        self.written += 1

    def mark(self) -> FakeMarker:
        if self.closed:
            raise SinkClosed("the sink is closed")
        self.marks += 1
        loop = asyncio.get_running_loop()
        marker = FakeMarker(self.marks, self.written, loop.create_future())
        self.markers.append(marker)
        self.resolve()
        return marker

    async def drain(self) -> bool:
        self.drains += 1
        if self.closed:
            return False
        return await self.mark()

    def clear(self, *, until: FakeMarker | None = None) -> None:
        if self.closed:
            return
        self.clears.append(None if until is None else until.id)
        limit = self.written if until is None else until.index
        while self.queue and self.queue[0][0] < limit:
            self.lose(self.queue.popleft()[0])
        self.playhead = max(self.playhead, limit)
        self.resolve()
        self.wake()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.queue.clear()
        for marker in self.markers:
            if not marker.future.done():
                marker.future.set_result(False)
        self.markers.clear()
        self.wake()
        self.vc.clock.sinks.remove(self)

    def stats(self) -> FakeStats:
        return FakeStats(self.playhead * FRAME)

    def pull(self) -> None:
        if not self.queue or (self.vc.held and self.stall == "hold"):
            self.ticks.append(None)
            return
        index, data = self.queue.popleft()
        self.playhead = index + 1
        if self.vc.held:
            self.lose(index)
            self.ticks.append(None)
        else:
            self.sent.append(data)
            self.ticks.append(data)
        self.resolve()
        self.wake()

    def lose(self, index: int) -> None:
        for marker in self.markers:
            if marker.index > index:
                marker.played = False

    def resolve(self) -> None:
        for marker in [m for m in self.markers if m.index <= self.playhead]:
            self.markers.remove(marker)
            if not marker.future.done():
                marker.future.set_result(marker.played)

    def wake(self) -> None:
        room, self.room = self.room, None
        if room is not None and not room.done():
            room.set_result(None)

    def gaps(self) -> int:
        played = [index for index, data in enumerate(self.ticks) if data is not None]
        return sum(data is None for data in self.ticks[played[0] : played[-1]])


class FakeConnection:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.manager = object()
        self.channel_id = 1
        self.state = State.READY
        self.held = False
        self.sinks: list[FakeSink] = []

    def sink(
        self,
        format: Format = DEFAULT_PCM,
        *,
        buffer: float | None = None,
        volume: float = 1.0,
        stall: SinkStall | None = None,
    ) -> FakeSink:
        if self.state is State.DISCONNECTED:
            raise VoiceNotConnected("the voice connection has ended")
        buffer = 0.2 if buffer is None else buffer
        sink = FakeSink(self, format, buffer, volume, stall or "hold")
        self.sinks.append(sink)
        self.clock.sinks.append(sink)
        return sink

    def disconnect(self) -> None:
        self.state = State.DISCONNECTED
        for sink in self.sinks:
            sink.close()


class FakeSource:
    def __init__(
        self,
        clock: Clock,
        name: str,
        length: int,
        *,
        format: Format = DEFAULT_PCM,
        probe: float = 0.0,
        open_error: Exception | None = None,
        error_at: int | None = None,
        seekable: bool = True,
    ) -> None:
        self.clock = clock
        self.name = name
        self.length = length
        self.probe = probe
        self.open_error = open_error
        self.error_at = error_at
        self.index = 0
        self.opened_at: list[float] = []
        self.managers: list[object] = []
        self.seeks: list[float] = []
        self.closed = False
        self._format = format
        self._seekable = seekable

    def __repr__(self) -> str:
        return f"FakeSource({self.name!r})"

    @property
    def format(self) -> Format:
        return self._format

    @property
    def seekable(self) -> bool:
        return self._seekable

    async def open(self, manager: object) -> None:
        self.opened_at.append(self.clock.now)
        self.managers.append(manager)
        if self.probe:
            await self.clock.sleep(self.probe)
        if self.open_error is not None:
            raise self.open_error

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        if self.index == self.error_at:
            raise OSError("the decoder failed")
        if self.closed or self.index >= self.length:
            raise StopAsyncIteration
        self.index += 1
        return f"{self.name}{self.index - 1}".encode()

    async def seek(self, position: float) -> None:
        self.seeks.append(position)
        self.index = round(position / FRAME)

    async def aclose(self) -> None:
        self.closed = True


class FakeEncoder:
    made: ClassVar[list[tuple[PCM, EncoderSettings]]] = []

    def __init__(self, format: PCM, *, encoder: EncoderSettings) -> None:
        self.made.append((format, encoder))

    def encode(self, data: Buffer) -> list[bytes]:
        return [b"opus-" + bytes(data)]

    def flush(self) -> list[bytes]:
        return [b"opus-tail"]


def run(scenario: Scenario) -> None:
    async def main() -> None:
        failures: list[dict[str, Any]] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: failures.append(context)
        )
        clock = Clock()
        ticking = asyncio.create_task(clock.run())
        try:
            async with asyncio.timeout(10):
                await scenario(clock)
        finally:
            ticking.cancel()
        assert failures == []

    asyncio.run(main())


async def play(vc: FakeConnection, source: FakeSource, **options: Any) -> Track:
    return await layers.play(cast(Any, vc), cast(Any, source), **options)


def queue(vc: FakeConnection, **options: Any) -> Queue:
    return Queue(cast(Any, vc), **options)


def add(q: Queue, source: FakeSource) -> Track:
    return q.add(cast(Any, source))


def reason(track: Track) -> EndReason | None:
    end = track.ended
    return None if end is None else end.reason


def test_a_played_track_finishes_once_its_sink_drained() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        source = FakeSource(clock, "a", 5)
        track = await play(vc, source, meta="intro")
        end = await track.wait()
        assert end.track is track
        assert track.ended is end
        assert (end.reason, end.error, end.may_start_next) == (
            EndReason.FINISHED,
            None,
            True,
        )
        assert track.meta == "intro"
        assert source.managers == [vc.manager]
        assert vc.sinks[0].sent == frames("a", 5)
        assert vc.sinks[0].drains == 1
        assert vc.sinks[0].closed
        assert source.closed

    run(scenario)


def test_stopping_a_played_track_clears_its_sink_at_once() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        source = FakeSource(clock, "a", 100)
        track = await play(vc, source)
        await clock.sleep(0.1)
        track.stop()
        end = track.ended
        assert end is not None
        assert (end.reason, end.may_start_next) == (EndReason.STOPPED, False)
        sink = vc.sinks[0]
        assert sink.clears == [None]
        assert sink.closed
        assert sink.sent == frames("a", 5)
        await clock.sleep(0.1)
        assert source.closed
        assert (await track.wait()).reason is EndReason.STOPPED

    run(scenario)


def test_a_source_that_fails_while_playing_ends_its_track_with_the_error() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        source = FakeSource(clock, "a", 20, error_at=3)
        track = await play(vc, source)
        end = await track.wait()
        assert (end.reason, end.may_start_next) == (EndReason.ERROR, True)
        assert isinstance(end.error, OSError)
        assert source.closed

    run(scenario)


def test_a_connection_that_ends_cleans_its_tracks_up() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        track = await play(vc, FakeSource(clock, "a", 100))
        await clock.sleep(0.1)
        vc.disconnect()
        end = await track.wait()
        assert (end.reason, end.may_start_next) == (EndReason.CLEANUP, False)

    run(scenario)


def test_play_raises_the_open_error_and_closes_the_source() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        source = FakeSource(clock, "a", 5, open_error=SourceError("a.mp3", "bad"))
        with pytest.raises(SourceError, match="bad"):
            await play(vc, source)
        assert vc.sinks == []
        assert source.closed

    run(scenario)


def test_play_on_an_ended_connection_closes_the_source() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        vc.state = State.DISCONNECTED
        source = FakeSource(clock, "a", 5)
        with pytest.raises(VoiceNotConnected):
            await play(vc, source)
        assert source.closed

    run(scenario)


def test_two_plays_open_two_sinks_that_both_play() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        first = await play(vc, FakeSource(clock, "a", 10))
        second = await play(vc, FakeSource(clock, "b", 10))
        await first.wait()
        await second.wait()
        assert [sink.sent for sink in vc.sinks] == [frames("a", 10), frames("b", 10)]

    run(scenario)


def test_sink_settings_reach_the_connection() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        track = await play(
            vc, FakeSource(clock, "a", 5), volume=0.5, buffer=0.4, stall="drop", meta=7
        )
        add(queue(vc, volume=0.7, buffer=0.1, stall="drop"), FakeSource(clock, "b", 5))
        await clock.until(lambda: len(vc.sinks) == 2)
        settings = [(sink.volume, sink.buffer, sink.stall) for sink in vc.sinks]
        assert settings == [(0.5, 0.4, "drop"), (0.7, 0.1, "drop")]
        assert track.meta == 7

    run(scenario)


def test_broadcast_passes_its_stall_to_every_sink() -> None:
    async def scenario(clock: Clock) -> None:
        dropping, holding = FakeConnection(clock), FakeConnection(clock)
        await layers.broadcast(
            cast(Any, FakeSource(clock, "a", 3, format=Opus())), [cast(Any, dropping)]
        )
        await layers.broadcast(
            cast(Any, FakeSource(clock, "b", 3, format=Opus())),
            [cast(Any, holding)],
            stall="hold",
        )
        assert [dropping.sinks[0].stall, holding.sinks[0].stall] == ["drop", "hold"]
        assert [dropping.sinks[0].format, holding.sinks[0].format] == [Opus(), Opus()]

    run(scenario)


def test_cancelling_one_wait_leaves_the_end_for_everyone_else() -> None:
    async def scenario(clock: Clock) -> None:
        q = queue(FakeConnection(clock))
        track = add(q, FakeSource(clock, "a", 10))
        waiter = asyncio.create_task(track.wait())
        await clock.sleep(0.04)
        waiter.cancel()
        await q.join()
        assert waiter.cancelled()
        assert reason(track) is EndReason.FINISHED
        assert (await track.wait()).reason is EndReason.FINISHED

    run(scenario)


def test_pause_stops_feeding_and_resume_carries_on() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        track = await play(vc, FakeSource(clock, "a", 40))
        await clock.sleep(0.1)
        track.pause()
        assert track.paused
        await clock.sleep(0.4)
        sink = vc.sinks[0]
        held = len(sink.sent)
        assert held <= 5 + sink.capacity + 1
        await clock.sleep(0.2)
        assert len(sink.sent) == held
        track.resume()
        assert not track.paused
        await track.wait()
        assert sink.sent == frames("a", 40)

    run(scenario)


def test_seek_clears_the_sink_and_restarts_the_position_at_the_target() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        source = FakeSource(clock, "a", 200)
        track = await play(vc, source)
        await clock.sleep(0.2)
        assert track.position == pytest.approx(0.2)
        await track.seek(2.0)
        sink = vc.sinks[0]
        assert sink.clears == [None]
        assert source.seeks == [2.0]
        assert track.position == 2.0
        before = len(sink.sent)
        await clock.sleep(0.4)
        assert track.position == pytest.approx(2.4)
        assert sink.sent[before : before + 3] == frames("a", 103, 100)
        assert sink.sent[:before] == frames("a", before)

    run(scenario)


def test_seeking_an_unseekable_source_raises_and_keeps_playing() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        track = await play(vc, FakeSource(clock, "a", 20, seekable=False))
        with pytest.raises(SourceError, match="not seekable"):
            await track.seek(1.0)
        await track.wait()
        assert vc.sinks[0].sent == frames("a", 20)
        with pytest.raises(RuntimeError, match="current track"):
            await track.seek(0.0)

    run(scenario)


def test_a_queue_writes_tracks_back_to_back_and_ends_them_at_their_markers() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        first = add(q, FakeSource(clock, "a", 20))
        second = add(q, FakeSource(clock, "b", 20))
        assert q.now is None
        assert q.upcoming == (first, second)
        await clock.until(lambda: q.now is first)
        assert list(q.upcoming) == [second]
        await first.wait()
        sink = vc.sinks[0]
        assert sink.playhead == 20
        assert q.now is second
        await q.join()
        assert len(vc.sinks) == 1
        assert sink.sent == frames("a", 20) + frames("b", 20)
        assert sink.gaps() == 0
        assert sink.drains == 1
        assert sink.closed
        assert q.now is None

    run(scenario)


def test_the_next_track_opens_when_the_current_one_starts() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        add(q, FakeSource(clock, "a", 100))
        slow = FakeSource(clock, "b", 20, probe=1.0)
        add(q, slow)
        await q.join()
        assert slow.opened_at == [0.0]
        assert vc.sinks[0].sent == frames("a", 100) + frames("b", 20)
        assert vc.sinks[0].gaps() == 0

    run(scenario)


def test_tracks_sharing_a_sink_keep_their_own_positions() -> None:
    async def scenario(clock: Clock) -> None:
        q = queue(FakeConnection(clock))
        first = add(q, FakeSource(clock, "a", 50))
        second = add(q, FakeSource(clock, "b", 50))
        await clock.until(lambda: clock.ticks == 25)
        assert q.now is first
        assert first.position == pytest.approx(0.5)
        assert second.position == 0.0
        await first.wait()
        assert first.position == pytest.approx(1.0)
        await clock.sleep(0.3)
        assert second.position == pytest.approx(0.3)
        assert first.position == pytest.approx(1.0)

    run(scenario)


def test_skip_while_feeding_the_current_track_clears_the_whole_sink() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        first = add(q, FakeSource(clock, "a", 100))
        second = add(q, FakeSource(clock, "b", 20))
        third = add(q, FakeSource(clock, "c", 20))
        await clock.sleep(0.2)
        q.skip()
        end = first.ended
        assert end is not None
        assert (end.reason, end.may_start_next) == (EndReason.STOPPED, False)
        await q.join()
        sink = vc.sinks[0]
        assert sink.clears == [None]
        assert sink.sent == frames("a", 10) + frames("b", 20) + frames("c", 20)
        assert [reason(second), reason(third)] == [EndReason.FINISHED] * 2

    run(scenario)


def test_skip_after_the_feeder_moved_on_keeps_the_next_head() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        first = add(q, FakeSource(clock, "a", 20))
        second = add(q, FakeSource(clock, "b", 20))
        third = add(q, FakeSource(clock, "c", 5))
        await clock.until(lambda: clock.ticks == 15)
        q.skip()
        await q.join()
        sink = vc.sinks[0]
        assert [until is None for until in sink.clears] == [False]
        assert sink.sent == frames("a", 15) + frames("b", 20) + frames("c", 5)
        assert sink.gaps() == 0
        assert [reason(first), reason(second), reason(third)] == [
            EndReason.STOPPED,
            EndReason.FINISHED,
            EndReason.FINISHED,
        ]

    run(scenario)


def test_seek_after_the_feeder_moved_on_rewrites_the_next_head() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        first_source = FakeSource(clock, "a", 50)
        second_source = FakeSource(clock, "b", 30)
        first = add(q, first_source)
        second = add(q, second_source)
        await clock.until(lambda: clock.ticks == 45)
        await first.seek(0.2)
        assert first.position == 0.2
        await q.join()
        sink = vc.sinks[0]
        assert sink.sent == frames("a", 45) + frames("a", 50, 10) + frames("b", 30)
        assert second_source.opened_at == [0.0]
        assert [reason(first), reason(second)] == [EndReason.FINISHED] * 2
        assert first.position == pytest.approx(1.0)

    run(scenario)


def test_a_format_change_drains_the_sink_and_opens_a_new_one() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        add(q, FakeSource(clock, "a", 10))
        packets = add(q, FakeSource(clock, "b", 10, format=Opus()))
        await clock.until(lambda: q.now is packets)
        assert packets.passthrough
        await q.join()
        assert [sink.format for sink in vc.sinks] == [PCM(), Opus()]
        assert [sink.sent for sink in vc.sinks] == [frames("a", 10), frames("b", 10)]
        assert [sink.closed for sink in vc.sinks] == [True, True]

    run(scenario)


def test_a_track_that_fails_to_open_ends_with_an_error_and_the_queue_goes_on() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        add(q, FakeSource(clock, "a", 10))
        failing = FakeSource(clock, "b", 10, open_error=SourceError("b.mp3", "bad"))
        broken = add(q, failing)
        add(q, FakeSource(clock, "c", 10))
        await q.join()
        end = broken.ended
        assert end is not None
        assert (end.reason, end.may_start_next) == (EndReason.ERROR, True)
        assert isinstance(end.error, SourceError)
        assert vc.sinks[0].sent == frames("a", 10) + frames("c", 10)
        assert failing.closed

    run(scenario)


def test_a_disconnect_cleans_every_track_up_and_closes_the_queue() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        tracks = [add(q, FakeSource(clock, name, 50)) for name in "abc"]
        await clock.sleep(0.1)
        vc.disconnect()
        await tracks[0].wait()
        assert [reason(track) for track in tracks] == [EndReason.CLEANUP] * 3
        assert q.closed
        with pytest.raises(VoiceNotConnected):
            add(q, FakeSource(clock, "d", 5))
        await q.join()

    run(scenario)


def test_a_disconnect_while_the_next_track_opens_cleans_the_playing_one_up() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        playing = add(q, FakeSource(clock, "a", 10))
        opening = add(q, FakeSource(clock, "b", 10, probe=1.0))
        await clock.sleep(0.1)
        vc.disconnect()
        await q.join()
        assert [reason(playing), reason(opening)] == [EndReason.CLEANUP] * 2
        assert clock.ticks < 50

    run(scenario)


def test_adding_to_a_queue_on_an_ended_connection_raises() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        vc.state = State.DISCONNECTED
        with pytest.raises(VoiceNotConnected):
            add(q, FakeSource(clock, "a", 5))
        assert q.closed

    run(scenario)


def test_stop_ends_every_track_before_it_returns() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        first = add(q, FakeSource(clock, "a", 100))
        second = add(q, FakeSource(clock, "b", 10))
        await clock.sleep(0.1)
        q.stop()
        assert [reason(first), reason(second)] == [EndReason.STOPPED] * 2
        assert vc.sinks[0].closed
        assert (q.now, q.upcoming, q.closed) == (None, (), False)
        third = add(q, FakeSource(clock, "c", 5))
        await q.join()
        assert reason(third) is EndReason.FINISHED
        assert vc.sinks[1].sent == frames("c", 5)

    run(scenario)


def test_join_waits_until_nothing_is_queued_or_playing() -> None:
    async def scenario(clock: Clock) -> None:
        q = queue(FakeConnection(clock))
        await q.join()
        tracks = [add(q, FakeSource(clock, name, 10)) for name in "ab"]
        await q.join()
        assert [reason(track) for track in tracks] == [EndReason.FINISHED] * 2
        assert clock.ticks >= 20

    run(scenario)


def test_removed_and_cleared_tracks_end_without_playing() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        add(q, FakeSource(clock, "a", 30))
        removed_source = FakeSource(clock, "b", 10)
        removed = add(q, removed_source)
        stopped = add(q, FakeSource(clock, "c", 10))
        cleared = add(q, FakeSource(clock, "d", 10))
        last = add(q, FakeSource(clock, "e", 10))
        await clock.sleep(0.1)
        q.remove(removed)
        stopped.stop()
        assert q.upcoming == (cleared, last)
        await clock.sleep(0.1)
        q.clear()
        assert [reason(removed), reason(stopped), reason(cleared)] == [
            EndReason.STOPPED
        ] * 3
        later = add(q, FakeSource(clock, "f", 10))
        await q.join()
        assert reason(last) is EndReason.STOPPED
        assert reason(later) is EndReason.FINISHED
        assert vc.sinks[0].sent == frames("a", 30) + frames("f", 10)
        assert removed_source.closed

    run(scenario)


def test_a_track_controls_its_queue_only_while_it_is_current() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        first = add(q, FakeSource(clock, "a", 30))
        second = add(q, FakeSource(clock, "b", 10))
        await clock.until(lambda: q.now is first)
        second.pause()
        assert not first.paused
        first.pause()
        assert first.paused
        assert not second.paused
        first.resume()
        second.volume = 0.3
        assert (q.volume, first.volume, vc.sinks[0].volume) == (0.3, 0.3, 0.3)
        with pytest.raises(RuntimeError, match="current track"):
            await second.seek(0.1)
        with pytest.raises(RuntimeError, match="has not started"):
            _ = second.sink
        first.stop()
        await q.join()
        assert vc.sinks[0].sent[-10:] == frames("b", 10)

    run(scenario)


def test_broadcast_writes_every_packet_once_to_each_connection() -> None:
    async def scenario(clock: Clock) -> None:
        connections = [FakeConnection(clock) for _ in range(3)]
        source = FakeSource(clock, "p", 10, format=Opus())
        await layers.broadcast(cast(Any, source), cast(Any, connections))
        assert [vc.sinks[0].sent for vc in connections] == [frames("p", 10)] * 3
        assert all(vc.sinks[0].closed for vc in connections)
        assert source.closed
        assert source.managers == [connections[0].manager]

    run(scenario)


def test_broadcast_is_not_delayed_by_a_connection_that_is_held() -> None:
    async def scenario(clock: Clock) -> None:
        live, held = FakeConnection(clock), FakeConnection(clock)
        held.held = True
        source = FakeSource(clock, "p", 50, format=Opus())
        await layers.broadcast(cast(Any, source), [cast(Any, live), cast(Any, held)])
        assert live.sinks[0].sent == frames("p", 50)
        assert live.sinks[0].gaps() == 0
        assert held.sinks[0].sent == []
        assert clock.ticks <= 52

    run(scenario)


def test_broadcast_goes_on_without_a_connection_that_ended() -> None:
    async def scenario(clock: Clock) -> None:
        connections = [FakeConnection(clock) for _ in range(3)]
        source = FakeSource(clock, "p", 30, format=Opus())
        playing = asyncio.create_task(
            layers.broadcast(cast(Any, source), cast(Any, connections))
        )
        await clock.sleep(0.1)
        connections[1].disconnect()
        await playing
        ended = connections[1].sinks[0].sent
        assert 0 < len(ended) < 30
        assert connections[0].sinks[0].sent == frames("p", 30)
        assert connections[2].sinks[0].sent == frames("p", 30)

    run(scenario)


def test_broadcast_stops_once_no_connection_is_left() -> None:
    async def scenario(clock: Clock) -> None:
        connections = [FakeConnection(clock) for _ in range(2)]
        ended = FakeConnection(clock)
        ended.state = State.DISCONNECTED
        source = FakeSource(clock, "p", 500, format=Opus())
        playing = asyncio.create_task(
            layers.broadcast(cast(Any, source), cast(Any, [*connections, ended]))
        )
        await clock.sleep(0.1)
        for vc in connections:
            vc.disconnect()
        await playing
        assert ended.sinks == []
        assert source.index < 500
        assert source.closed

    run(scenario)


def test_broadcast_encodes_pcm_once_for_every_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(layers, "OpusEncoder", FakeEncoder)
    monkeypatch.setattr(FakeEncoder, "made", [])

    async def scenario(clock: Clock) -> None:
        connections = [FakeConnection(clock) for _ in range(2)]
        pcm = PCM(24_000, 1)
        source = FakeSource(clock, "s", 3, format=pcm)
        await layers.broadcast(cast(Any, source), cast(Any, connections))
        expected = [b"opus-" + chunk for chunk in frames("s", 3)] + [b"opus-tail"]
        assert [vc.sinks[0].sent for vc in connections] == [expected] * 2
        assert FakeEncoder.made == [(pcm, EncoderSettings())]
        voip = EncoderSettings(application="voip")
        await layers.broadcast(
            cast(Any, FakeSource(clock, "t", 1, format=pcm)),
            cast(Any, connections),
            encoder=voip,
        )
        assert FakeEncoder.made[-1] == (pcm, voip)

    run(scenario)


STEP = 960
SPEECH_PCM = PCM(8_000, 2, "f32le")
SPEECH_BYTES = 1280
SILENT = b"\xf8\xff\xfe"
OGG_FIXTURE = bytes.fromhex(
    "4f676753000200000000000000002a000000000000004fd5ab2601134f70757348656164"
    "0102000080bb00000000004f676753000000000000000000002a000000010000003236988a"
    "01164f7075735461677306000000626f73746965000000004f6767530004c0030000000000"
    "002a00000002000000f4e8daaf0103fcfffe"
)

type Received = Frame | SpeechStart | SpeechStop


class FakeReceiver:
    def __init__(
        self,
        format: ReceiveFormat = SPEECH_PCM,
        *,
        markers: bool = True,
        mix: bool = False,
        jitter: float = 0.1,
    ) -> None:
        self.format = format
        self.markers = markers
        self.mix = mix
        self.jitter = jitter
        self.items: deque[Received] = deque()
        self.buffered: list[Received] = []
        self.marks: list[asyncio.Future[bool]] = []
        self.closed_reason: ClosedReason | None = None
        self.arrival: asyncio.Future[None] | None = None

    def feed(self, *items: Received) -> None:
        self.items.extend(items)
        self.wake()

    def hear(self, *items: Received) -> None:
        self.buffered.extend(items)

    def release(self) -> None:
        self.feed(*self.buffered)
        self.buffered.clear()
        self.settle_marks(True)

    def mark(self) -> asyncio.Future[bool]:
        future = asyncio.get_running_loop().create_future()
        self.marks.append(future)
        if self.closed_reason is not None:
            self.settle_marks(False)
        elif not self.buffered:
            self.settle_marks(True)
        return future

    def settle_marks(self, passed: bool) -> None:
        for future in self.marks:
            future.set_result(passed)
        self.marks.clear()

    def end(self, reason: ClosedReason) -> None:
        self.closed_reason = self.closed_reason or reason
        self.settle_marks(False)
        self.wake()

    def close(self) -> None:
        self.items.clear()
        self.end(ClosedReason.CLOSED)

    def get_nowait(self) -> Received:
        if self.items:
            return self.items.popleft()
        if self.closed_reason is not None:
            raise ReceiverClosed("the receiver ended and has nothing left to read")
        raise Empty("nothing is queued in the receiver")

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Received:
        while not self.items:
            if self.closed_reason is not None:
                raise StopAsyncIteration
            self.arrival = asyncio.get_running_loop().create_future()
            await self.arrival
        return self.items.popleft()

    def wake(self) -> None:
        if self.arrival is not None and not self.arrival.done():
            self.arrival.set_result(None)


class RecordingConnection:
    guild_id = 7

    def __init__(self) -> None:
        self.rx = FakeReceiver(Opus(), markers=False)
        self.opened: list[tuple[ReceiveFormat, dict[str, Any]]] = []

    def receive(self, format: ReceiveFormat, **options: Any) -> FakeReceiver:
        self.opened.append((format, options))
        return self.rx


async def settle() -> None:
    for _ in range(SETTLE):
        await asyncio.sleep(0)


def frame(user: int, index: int, *, lost: bool = False) -> Frame:
    data = bytes([user * 16 + index]) * SPEECH_BYTES
    return Frame(user, user, index * STEP, index * STEP, STEP, 0.0, data, lost)


def start(user: int, index: int) -> SpeechStart:
    return SpeechStart(guild_id=7, user_id=user, ssrc=user, at=0.0, offset=index * STEP)


def stop(user: int, index: int, *, at: float = 0.0) -> SpeechStop:
    return SpeechStop(
        guild_id=7, user_id=user, ssrc=user, at=at, duration=0.0, offset=index * STEP
    )


def heard(user: int, *indexes: int | None, lost: int = 0) -> Utterance:
    data = b"".join(
        bytes(SPEECH_BYTES) if index is None else frame(user, index).data
        for index in indexes
    )
    first = next(index for index in indexes if index is not None)
    return Utterance(
        user, data, first * STEP, len(indexes) * STEP / 48_000, lost * STEP / 48_000
    )


def opus(index: int, data: bytes | None = None, *, lost: bool = False) -> Frame:
    packet = bytes([index]) * 3 if data is None else data
    return Frame(42, 42, index * STEP, index * STEP, STEP, 0.0, packet, lost)


def read_ogg(path: Path) -> tuple[list[bytes], list[int]]:
    data = path.read_bytes()
    packets: list[bytes] = []
    granules: list[int] = []
    partial = b""
    while data:
        count = data[26]
        page = bytearray(data[: 27 + count + sum(data[27 : 27 + count])])
        page[22:26] = bytes(4)
        assert ogg_crc(page) == int.from_bytes(data[22:26], "little")
        body = data[27 + count :]
        granules.append(int.from_bytes(data[6:14], "little"))
        for size in data[27 : 27 + count]:
            partial += body[:size]
            body = body[size:]
            if size < 255:
                packets.append(partial)
                partial = b""
        data = body
    return packets[2:], granules


def ogg_crc(page: bytearray) -> int:
    crc = 0
    for byte in page:
        crc ^= byte << 24
        for _ in range(8):
            crc = (crc << 1) ^ 0x104C11DB7 if crc & 0x80000000 else crc << 1
    return crc


@pytest.mark.parametrize(
    "rx",
    [
        FakeReceiver(Opus()),
        FakeReceiver(markers=False),
        FakeReceiver(mix=True),
    ],
    ids=["opus", "no-markers", "mix"],
)
def test_utterances_refuses_a_receiver_it_cannot_group_at_the_call(
    rx: FakeReceiver,
) -> None:
    with pytest.raises(ValueError, match="markers=True"):
        utterances(cast(Any, rx))


@pytest.mark.parametrize(
    ("items", "max_length", "expected"),
    [
        pytest.param(
            [
                *(start(1, 1), frame(1, 1), frame(1, 2), stop(1, 2)),
                *(start(1, 6), frame(1, 6)),
            ],
            30.0,
            [heard(1, 1, 2, None, None, None, 6)],
            id="a-shorter-pause-is-filled-with-zeros",
        ),
        pytest.param(
            [
                *(start(1, 1), frame(1, 1), frame(1, 2), stop(1, 2)),
                *(start(1, 7), frame(1, 7)),
            ],
            30.0,
            [heard(1, 1, 2), heard(1, 7)],
            id="min-silence-counts-from-the-stop",
        ),
        pytest.param(
            [start(1, 1), *(frame(1, index) for index in range(1, 6))],
            0.06,
            [heard(1, 1, 2, 3), heard(1, 4, 5)],
            id="max-length-cuts",
        ),
        pytest.param(
            [frame(1, 1), frame(1, 2), frame(1, 8)],
            30.0,
            [heard(1, 1, 2), heard(1, 8)],
            id="a-gap-without-a-stop",
        ),
        pytest.param(
            [start(1, 1), frame(1, 1), start(1, 2), frame(1, 2)],
            30.0,
            [heard(1, 1), heard(1, 2)],
            id="a-second-start",
        ),
        pytest.param(
            [start(1, 1), frame(1, 1), frame(1, 2, lost=True), frame(1, 3, lost=True)],
            30.0,
            [heard(1, 1, 2, 3, lost=2)],
            id="lost-seconds",
        ),
        pytest.param(
            [
                *(start(1, 1), start(2, 1), frame(1, 1), frame(2, 1)),
                *(stop(1, 2), frame(2, 2)),
            ],
            30.0,
            [heard(1, 1), heard(2, 1, 2)],
            id="one-per-user",
        ),
    ],
)
def test_utterances_split_speech_at_stops_gaps_starts_and_max_length(
    items: list[Received], max_length: float, expected: list[Utterance]
) -> None:
    async def scenario(clock: Clock) -> None:
        rx = FakeReceiver()
        rx.feed(*items)
        rx.end(ClosedReason.DISCONNECTED)
        grouped = utterances(cast(Any, rx), min_silence=0.1, max_length=max_length)
        assert [utterance async for utterance in grouped] == expected

    run(scenario)


def test_an_utterance_ends_jitter_and_min_silence_after_its_stop() -> None:
    async def scenario(clock: Clock) -> None:
        rx = FakeReceiver(jitter=10.0)
        grouped: list[Utterance] = []

        async def listen() -> None:
            async for utterance in utterances(cast(Any, rx), min_silence=5.0):
                grouped.append(utterance)

        listening = asyncio.create_task(listen())
        rx.feed(start(1, 1), frame(1, 1), stop(1, 2, at=time.monotonic() - 10.0))
        await settle()
        assert grouped == []
        rx.feed(start(1, 4), frame(1, 4), stop(1, 5, at=time.monotonic() - 20.0))
        await settle()
        assert grouped == [heard(1, 1, None, None, 4)]
        rx.end(ClosedReason.DISCONNECTED)
        await listening
        assert len(grouped) == 1

    run(scenario)


def test_a_recorder_writes_ogg_opus_pages_into_its_path_template(
    tmp_path: Path,
) -> None:
    async def scenario(clock: Clock) -> None:
        vc = RecordingConnection()
        template = tmp_path / "{guild_id}" / "{user_id}.opus"
        async with Recorder(cast(Any, vc), template) as recorder:
            vc.rx.feed(Frame(42, 42, 0, 0, STEP, 0.0, b"\xfc\xff\xfe", False))
        path = tmp_path / "7" / "42.opus"
        assert recorder.files == {42: path}
        assert path.read_bytes() == OGG_FIXTURE
        assert vc.opened == [
            (
                Opus(),
                {
                    "users": None,
                    "exclude": (),
                    "include_self": False,
                    "buffer": 30.0,
                    "jitter": 0.3,
                    "stall": "wait",
                },
            )
        ]

    run(scenario)


@pytest.mark.parametrize(
    ("align", "expected"),
    [
        (
            True,
            (
                [
                    *(SILENT, SILENT, b"\x02" * 3, b"\x03" * 3, SILENT, b"\x05" * 3),
                    *(SILENT, b"\x07" * 3, *[SILENT] * 102, b"n" * 3),
                ],
                [0, 0, 48_000, 96_000, 106_560],
            ),
        ),
        (
            False,
            (
                [b"\x02" * 3, b"\x03" * 3, b"\x05" * 3, b"\x07" * 3, b"n" * 3],
                [0, 0, 4_800],
            ),
        ),
    ],
    ids=["aligned", "speech-only"],
)
def test_a_recorder_fills_the_start_and_every_gap_with_silence_only_when_aligned(
    tmp_path: Path, align: bool, expected: tuple[list[bytes], list[int]]
) -> None:
    async def scenario(clock: Clock) -> None:
        vc = RecordingConnection()
        recorder = Recorder(cast(Any, vc), tmp_path / "{user_id}.ogg", align=align)
        vc.rx.feed(opus(2), opus(3), opus(4, b"", lost=True), opus(5))
        vc.rx.feed(opus(6, bytes(65_025)), opus(7), opus(110))
        await recorder.stop()
        assert read_ogg(tmp_path / "42.ogg") == expected

    run(scenario)


@pytest.mark.parametrize(
    "path",
    [
        "rec/{user_id}.wav",
        "rec/everyone.ogg",
        "rec/{channel_id}/{user_id}.ogg",
        "rec/{}{user_id}.ogg",
    ],
)
def test_a_recorder_refuses_a_path_that_cannot_hold_one_ogg_file_per_user(
    path: str,
) -> None:
    vc = RecordingConnection()
    with pytest.raises(ValueError, match="path"):
        Recorder(cast(Any, vc), path)
    assert vc.opened == []


def test_stop_waits_for_the_audio_still_in_the_jitter_buffer(tmp_path: Path) -> None:
    async def scenario(clock: Clock) -> None:
        vc = RecordingConnection()
        recorder = Recorder(cast(Any, vc), tmp_path / "{user_id}.ogg", align=False)
        await settle()
        vc.rx.feed(*(opus(index) for index in range(5)))
        vc.rx.hear(opus(5))
        stopping = asyncio.create_task(recorder.stop())
        await settle()
        waited = not stopping.done()
        vc.rx.release()
        await stopping
        packets, _ = read_ogg(tmp_path / "42.ogg")
        assert waited
        assert packets == [bytes([index]) * 3 for index in range(6)]

    run(scenario)


def test_a_recorder_raises_its_write_error_from_stop(tmp_path: Path) -> None:
    async def scenario(clock: Clock) -> None:
        (tmp_path / "taken").write_bytes(b"")
        vc = RecordingConnection()
        recorder = Recorder(cast(Any, vc), tmp_path / "taken" / "{user_id}.ogg")
        vc.rx.feed(opus(0))
        with pytest.raises(OSError):
            await recorder.stop()
        assert vc.rx.closed_reason is ClosedReason.CLOSED

    run(scenario)


def test_a_recorder_finishes_its_files_when_its_receiver_moved_away(
    tmp_path: Path,
) -> None:
    async def scenario(clock: Clock) -> None:
        vc = RecordingConnection()
        recorder = Recorder(cast(Any, vc), tmp_path / "{user_id}.ogg", align=False)
        await settle()
        vc.rx.feed(opus(0), opus(1))
        vc.rx.end(ClosedReason.MOVED)
        await settle()
        files = await recorder.stop()
        assert files == {42: tmp_path / "42.ogg"}
        assert read_ogg(files[42]) == ([b"\x00" * 3, b"\x01" * 3], [0, 0, 1_920])

    run(scenario)
