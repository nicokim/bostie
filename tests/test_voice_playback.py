import asyncio
from collections import deque
from collections.abc import Buffer, Callable, Coroutine, Generator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, ClassVar, Self, cast, override

import pytest

from bostie.enums import EndReason, State
from bostie.errors import (
    SinkClosed,
    SourceError,
    VoiceNotConnected,
)
from bostie.testing import settle
from bostie.voice import player
from bostie.voice.formats import (
    DEFAULT_PCM,
    PCM,
    Defaults,
    EncoderSettings,
    Filters,
    Format,
    Opus,
)
from bostie.voice.player import Queue, Track

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
            await settle(SETTLE)
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
        after: "FakeSink | None" = None,
        filters: Filters | None = None,
    ) -> None:
        self.vc = vc
        self.filters = filters or Filters()
        self.born = vc.clock.ticks + 1
        self.after = after
        self.opened_after = after
        self.crossfade = 0.0
        self.flushed = False
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
        self.cuts: list[int] = []
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
        self.flushed = False

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
        self.flushed = True
        return await self.mark()

    def clear(
        self, *, until: FakeMarker | None = None, since: FakeMarker | None = None
    ) -> None:
        if self.closed:
            return
        if since is not None:
            self.cuts.append(since.id)
            start = max(since.index, self.playhead)
            self.queue = deque(item for item in self.queue if item[0] < start)
            self.written = start
            for marker in [m for m in self.markers if m.index > start]:
                self.markers.remove(marker)
                marker.future.set_result(False)
            self.wake()
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

    def waiting(self) -> bool:
        after = self.after
        if after is None:
            return False
        playing = bool(after.ticks) and after.ticks[-1] is not None
        if after.closed or (after.flushed and not after.queue and not playing):
            self.after = None
            self.resolve()
            return False
        return True

    def pull(self) -> None:
        if self.waiting() or not self.queue:
            self.ticks.append(None)
            return
        index, data = self.queue.popleft()
        self.playhead = index + 1
        self.sent.append(data)
        self.ticks.append(data)
        self.resolve()
        self.wake()

    def lose(self, index: int) -> None:
        for marker in self.markers:
            if marker.index > index:
                marker.played = False

    def resolve(self) -> None:
        if self.after is not None:
            return
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
        self.manager = SimpleNamespace(defaults=Defaults())
        self.channel_id = 1
        self.state = State.READY
        self.sinks: list[FakeSink] = []

    def sink(
        self,
        format: Format = DEFAULT_PCM,
        *,
        volume: float = 1.0,
        after: FakeSink | None = None,
        crossfade: float = 0.0,
        filters: Filters | None = None,
        buffer: float | None = None,
        stall: str | None = None,
    ) -> FakeSink:
        if self.state is State.DISCONNECTED:
            raise VoiceNotConnected("the voice connection has ended")
        buffer = 0.2 if buffer is None else buffer
        stall = "hold" if stall is None else stall
        sink = FakeSink(self, format, buffer, volume, stall, after, filters)
        sink.crossfade = crossfade
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
        repeats: bool = True,
    ) -> None:
        self.clock = clock
        self.repeats = repeats
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

    @override
    def __repr__(self) -> str:
        return f"FakeSource({self.name!r})"

    def again(self) -> "FakeSource":
        if not self.repeats:
            raise SourceError(repr(self), "cannot play again")
        return FakeSource(self.clock, self.name, self.length, format=self._format)

    @property
    def format(self) -> Format:
        return self._format

    @property
    def opened(self) -> bool:
        return bool(self.opened_at)

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
    return await player.play(cast("Any", vc), cast("Any", source), **options)


def queue(vc: FakeConnection, **options: Any) -> Queue:
    return Queue(cast("Any", vc), **options)


def add(q: Queue, source: FakeSource, index: int | None = None) -> Track:
    return q.add(cast("Any", source), index=index)


def names(chunks: list[bytes]) -> str:
    return "".join(chunk[:1].decode() for chunk in chunks)


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


def test_play_raises_the_open_error_and_closes_the_source() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        source = FakeSource(clock, "a", 5, open_error=SourceError("a.mp3", "bad"))
        with pytest.raises(SourceError, match="bad"):
            await play(vc, source)
        assert vc.sinks == []
        assert source.closed

    run(scenario)


def test_sink_settings_reach_the_connection() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        bass = Filters(bass=6.0)
        track = await play(
            vc, FakeSource(clock, "a", 5), volume=0.5, buffer=0.4, stall="drop", meta=7
        )
        loud = queue(vc, volume=0.7, filters=bass, buffer=0.1, stall="drop")
        add(loud, FakeSource(clock, "b", 5))
        await clock.until(lambda: len(vc.sinks) == 2)
        packets = FakeSource(clock, "c", 3, format=Opus())
        await player.broadcast(cast("Any", packets), [cast("Any", vc)])
        await player.broadcast(
            cast("Any", FakeSource(clock, "d", 3, format=Opus())),
            [cast("Any", vc)],
            stall="hold",
        )
        settings = [(sink.volume, sink.buffer, sink.stall) for sink in vc.sinks]
        assert settings == [
            (0.5, 0.4, "drop"),
            (0.7, 0.1, "drop"),
            (1.0, 0.2, "drop"),
            (1.0, 0.2, "hold"),
        ]
        assert [sink.format for sink in vc.sinks[2:]] == [Opus(), Opus()]
        assert [sink.filters for sink in vc.sinks[:2]] == [Filters(), bass]
        assert track.meta == 7

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
        assert (track.paused, track.ended) == (True, None)
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
        await q.join()
        first = add(q, FakeSource(clock, "a", 20))
        second = add(q, FakeSource(clock, "b", 20))
        assert (q.now, q.upcoming) == (None, (first, second))
        await clock.until(lambda: q.now is first)
        assert list(q.upcoming) == [second]
        await first.wait()
        sink = vc.sinks[0]
        assert (sink.playhead, q.now) == (20, second)
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


def test_a_format_change_opens_a_sink_that_starts_as_the_last_one_ends() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        add(q, FakeSource(clock, "a", 10))
        packets = add(q, FakeSource(clock, "b", 10, format=Opus()))
        add(q, FakeSource(clock, "c", 10))
        await clock.until(lambda: q.now is packets)
        assert packets.passthrough
        await q.join()
        assert [sink.format for sink in vc.sinks] == [PCM(), Opus(), PCM()]
        assert [sink.after for sink in vc.sinks] == [None] * 3
        heard: dict[int, list[bytes]] = {}
        for sink in vc.sinks:
            for tick, data in enumerate(sink.ticks, sink.born):
                if data is not None:
                    heard.setdefault(tick, []).append(data)
        ticks = sorted(heard)
        assert ticks == list(range(ticks[0], ticks[0] + 30))
        assert vc.sinks[1].born < ticks[0] + 10
        assert [heard[tick] for tick in ticks] == [
            [chunk] for chunk in frames("a", 10) + frames("b", 10) + frames("c", 10)
        ]
        assert [sink.closed for sink in vc.sinks] == [True, True, True]

    run(scenario)


def test_skip_and_stop_reach_a_track_still_playing_on_the_last_sink() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        first = add(q, FakeSource(clock, "a", 10))
        second = add(q, FakeSource(clock, "b", 50, format=Opus()))
        await clock.until(lambda: len(vc.sinks) == 2 and clock.ticks == 3)
        q.skip()
        assert await second.started()
        q.stop()
        assert [reason(first), reason(second)] == [EndReason.STOPPED] * 2
        assert names(vc.sinks[0].sent) == "aaa"
        assert names(vc.sinks[1].sent) == "b"
        assert [sink.closed for sink in vc.sinks] == [True, True]

    run(scenario)


def test_loop_track_repeats_until_turned_off_and_skip_moves_on() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        q.loop = "track"
        add(q, FakeSource(clock, "a", 5))
        add(q, FakeSource(clock, "b", 5))
        await clock.until(lambda: clock.ticks == 12)
        q.skip()
        await clock.until(lambda: clock.ticks == 30)
        q.loop = None
        await q.join()
        assert names(vc.sinks[0].sent) == "a" * 12 + "b" * 20

    run(scenario)


def test_loop_queue_repeats_every_track_and_once_only_sources_drop_out() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        add(q, FakeSource(clock, "a", 3))
        add(q, FakeSource(clock, "b", 3, repeats=False))
        add(q, FakeSource(clock, "c", 3))
        q.loop = "queue"
        assert q.loop == "queue"
        await clock.until(lambda: clock.ticks == 21)
        q.stop()
        assert names(vc.sinks[0].sent) == "aaabbbcccaaacccaaaccc"
        with pytest.raises(ValueError, match="loop"):
            q.loop = cast("Any", "forever")

    run(scenario)


def test_loop_queue_turned_on_mid_track_repeats_a_track_written_ahead() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        add(q, FakeSource(clock, "a", 3))
        written_ahead = FakeSource(clock, "b", 3)
        add(q, written_ahead)
        await clock.until(lambda: clock.ticks == 1)
        assert written_ahead.index > 0
        q.loop = "queue"
        await clock.until(lambda: clock.ticks == 12)
        q.stop()
        assert names(vc.sinks[0].sent) == "aaabbbaaabbb"

    run(scenario)


def test_back_replays_the_last_played_track_then_the_current_one() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc, history=2)
        first = add(q, FakeSource(clock, "a", 3))
        second = add(q, FakeSource(clock, "b", 4))
        add(q, FakeSource(clock, "c", 2))
        await clock.until(lambda: second.position > 0)
        played = q.history
        replay = q.back()
        assert (played, q.history, reason(second)) == ((first,), (), EndReason.STOPPED)
        assert replay is not None
        await q.join()
        assert names(vc.sinks[0].sent) == "aaab" + "aaa" + "bbbb" + "cc"
        assert reason(replay) is EndReason.FINISHED
        assert [cast("FakeSource", t.source).name for t in q.history] == ["b", "c"]

    run(scenario)


def test_back_needs_a_history_and_a_source_that_plays_again() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        kept = queue(vc, history=1)
        add(kept, FakeSource(clock, "a", 2, repeats=False))
        await kept.join()
        with pytest.raises(SourceError, match="again"):
            kept.back()
        assert len(kept.history) == 1
        forgetful = queue(vc)
        add(forgetful, FakeSource(clock, "b", 2))
        await forgetful.join()
        assert (forgetful.history, forgetful.back()) == ((), None)
        vc.manager.defaults = Defaults(history=3)
        remembering = queue(vc)
        add(remembering, FakeSource(clock, "c", 1))
        await remembering.join()
        assert len(remembering.history) == 1
        with pytest.raises(ValueError, match="history"):
            queue(vc, history=-1)

    run(scenario)


def test_a_crossfading_queue_chains_one_sink_per_track() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc, crossfade=2.0)
        add(q, FakeSource(clock, "a", 5))
        add(q, FakeSource(clock, "b", 5))
        await q.join()
        assert [sink.sent for sink in vc.sinks] == [frames("a", 5), frames("b", 5)]
        assert [sink.opened_after for sink in vc.sinks] == [None, vc.sinks[0]]
        assert [sink.crossfade for sink in vc.sinks] == [2.0, 2.0]
        vc.manager.defaults = Defaults(crossfade=1.5)
        assert (queue(vc).crossfade, queue(vc, crossfade=0.0).crossfade) == (1.5, 0.0)
        with pytest.raises(ValueError, match="crossfade"):
            queue(vc, crossfade=-1.0)

    run(scenario)


def test_a_track_that_fails_to_open_ends_in_error_and_the_queue_goes_on() -> None:
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


def test_started_waits_for_the_audio_and_is_false_for_a_track_never_played() -> None:
    async def scenario(clock: Clock) -> None:
        q = queue(FakeConnection(clock))
        first = add(q, FakeSource(clock, "a", 20))
        second = add(q, FakeSource(clock, "b", 10))
        removed = add(q, FakeSource(clock, "c", 10))
        assert await first.started()
        q.remove(removed)
        assert await second.started()
        assert clock.ticks == 20
        assert not await removed.started()

    run(scenario)


def test_closing_the_sink_from_outside_stops_the_queue_but_keeps_it_open() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        tracks = [add(q, FakeSource(clock, name, 50)) for name in "ab"]
        await clock.sleep(0.1)
        vc.sinks[0].close()
        await q.join()
        assert [reason(track) for track in tracks] == [EndReason.STOPPED] * 2
        assert not q.closed
        later = add(q, FakeSource(clock, "c", 5))
        await q.join()
        assert reason(later) is EndReason.FINISHED
        assert vc.sinks[1].sent == frames("c", 5)

    run(scenario)


def test_tracks_go_in_at_an_index_and_move_among_the_upcoming_ones() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        first = add(q, FakeSource(clock, "a", 30))
        later = add(q, FakeSource(clock, "c", 5))
        await clock.until(lambda: q.now is first)
        nearer = add(q, FakeSource(clock, "b", 5), index=0)
        last = add(q, FakeSource(clock, "d", 5))
        q.move(last, 1)
        assert q.upcoming == (nearer, last, later)
        with pytest.raises(ValueError, match="upcoming"):
            q.move(first, 0)
        await q.join()
        assert names(vc.sinks[0].sent) == "a" * 30 + "b" * 5 + "d" * 5 + "c" * 5

    run(scenario)


def test_moving_a_track_written_ahead_rewrites_it_after_the_head() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        add(q, FakeSource(clock, "a", 15))
        ahead = add(q, FakeSource(clock, "b", 20))
        add(q, FakeSource(clock, "c", 5))
        await clock.until(lambda: clock.ticks == 10)
        q.move(ahead, 1)
        await q.join()
        sink = vc.sinks[0]
        assert sink.sent == frames("a", 15) + frames("c", 5) + frames("b", 20)
        assert (len(sink.cuts), sink.clears, sink.gaps()) == (1, [], 0)

    run(scenario)


def test_shuffle_reorders_the_upcoming_tracks_and_plays_each_once() -> None:
    async def scenario(clock: Clock) -> None:
        vc = FakeConnection(clock)
        q = queue(vc)
        first = add(q, FakeSource(clock, "a", 5))
        rest = [add(q, FakeSource(clock, name, 2)) for name in "bcdefghijk"]
        await clock.until(lambda: q.now is first)
        q.shuffle()
        assert sorted(q.upcoming, key=rest.index) == rest
        order = "".join(cast("FakeSource", track.source).name for track in q.upcoming)
        await q.join()
        assert names(vc.sinks[0].sent) == "a" * 5 + "".join(name * 2 for name in order)

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
        assert (first.paused, second.paused) == (False, False)
        first.pause()
        assert (first.paused, second.paused) == (True, False)
        first.resume()
        second.volume = 0.3
        assert (q.volume, first.volume, vc.sinks[0].volume) == (0.3, 0.3, 0.3)
        second.filters = Filters(speed=1.5)
        assert q.filters == first.filters == vc.sinks[0].filters == Filters(speed=1.5)
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
        await player.broadcast(cast("Any", source), cast("Any", connections))
        assert [vc.sinks[0].sent for vc in connections] == [frames("p", 10)] * 3
        assert all(vc.sinks[0].closed for vc in connections)
        assert source.closed
        assert source.managers == [connections[0].manager]

    run(scenario)


def test_broadcast_goes_on_without_a_connection_that_ended() -> None:
    async def scenario(clock: Clock) -> None:
        connections = [FakeConnection(clock) for _ in range(3)]
        source = FakeSource(clock, "p", 30, format=Opus())
        playing = asyncio.create_task(
            player.broadcast(cast("Any", source), cast("Any", connections))
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
            player.broadcast(cast("Any", source), cast("Any", [*connections, ended]))
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
    monkeypatch.setattr(player, "OpusEncoder", FakeEncoder)
    monkeypatch.setattr(FakeEncoder, "made", [])

    async def scenario(clock: Clock) -> None:
        connections = [FakeConnection(clock) for _ in range(2)]
        pcm = PCM(24_000, 1)
        source = FakeSource(clock, "s", 3, format=pcm)
        await player.broadcast(cast("Any", source), cast("Any", connections))
        expected = [b"opus-" + chunk for chunk in frames("s", 3)] + [b"opus-tail"]
        assert [vc.sinks[0].sent for vc in connections] == [expected] * 2
        assert FakeEncoder.made == [(pcm, EncoderSettings())]
        voip = EncoderSettings(application="voip")
        await player.broadcast(
            cast("Any", FakeSource(clock, "t", 1, format=pcm)),
            cast("Any", connections),
            encoder=voip,
        )
        assert FakeEncoder.made[-1] == (pcm, voip)

    run(scenario)
