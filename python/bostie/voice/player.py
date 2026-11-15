import asyncio
import contextlib
import logging
import random
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum, StrEnum, auto
from typing import TYPE_CHECKING, Any, Literal, Unpack

from bostie._aio import spawn
from bostie.enums import State
from bostie.errors import SinkClosed, SourceError, StateError, VoiceNotConnected
from bostie.models.base import check_keys
from bostie.voice.encoder import DEFAULT_ENCODER, OpusEncoder
from bostie.voice.formats import (
    NO_FILTERS,
    PCM,
    SINK_OPTIONS,
    EncoderSettings,
    Filters,
    Format,
    Opus,
    SinkOptions,
    SinkStall,
    check_crossfade,
)

if TYPE_CHECKING:
    from bostie.voice.connection import VoiceConnection
    from bostie.voice.sink import Marker, Sink
    from bostie.voice.sources import Source

log = logging.getLogger("bostie.voice")

type Loop = Literal["track", "queue"] | None


class EndReason(StrEnum):
    FINISHED = "finished"
    STOPPED = "stopped"
    ERROR = "error"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class TrackEnd:
    """How a track ended; `may_start_next` says whether a player should go on."""

    track: "Track"
    reason: EndReason
    error: BaseException | None
    may_start_next: bool


class _Stage(Enum):
    QUEUED = auto()
    OPENING = auto()
    OPEN = auto()
    WRITING = auto()
    WRITTEN = auto()
    ENDED = auto()


WRITTEN_STAGES = frozenset({_Stage.WRITING, _Stage.WRITTEN})


@dataclass(eq=False, slots=True)
class _Entry:
    source: "Source"
    meta: Any
    done: asyncio.Future[TrackEnd]
    began: asyncio.Future[bool]
    track: "Track" = field(init=False)
    stage: _Stage = _Stage.QUEUED
    opening: asyncio.Task[Exception | None] | None = None
    sink: "Sink | None" = None
    start: "Marker | None" = None
    end: "Marker | None" = None
    base: float = 0.0
    exhausted: bool = False
    kept: list[bytes] = field(default_factory=list[bytes])
    final: float | None = None
    origin: "_Entry | None" = None
    repeated: bool = False

    @property
    def written(self) -> bool:
        return self.stage in WRITTEN_STAGES

    def unwrite(self) -> None:
        if self.written:
            self.stage = _Stage.OPEN
        self.start = self.end = None

    def position(self) -> float:
        if self.final is not None:
            return self.final
        if self.sink is None or self.start is None:
            return self.base
        played = self.sink.stats().position - self.start.position
        if self.end is not None:
            played = min(played, self.end.position - self.start.position)
        return self.base + max(played, 0.0)


class Track:
    """A source playing, or waiting to play, on a voice connection."""

    def __init__(
        self,
        queue: "Queue",
        entry: _Entry,
        *,
        current: Callable[[_Entry], bool],
        seek: Callable[[_Entry, float], Awaitable[None]],
        stop: Callable[[_Entry], None],
    ) -> None:
        self.source = entry.source
        self.meta = entry.meta
        self._queue = queue
        self._entry = entry
        self._current = current
        self._seek = seek
        self._stop = stop

    @property
    def sink(self) -> "Sink":
        """The sink this track is written to; StateError until it starts."""
        if self._entry.sink is None:
            raise StateError("the track has not started")
        return self._entry.sink

    @property
    def ended(self) -> TrackEnd | None:
        done = self._entry.done
        return done.result() if done.done() else None

    async def wait(self) -> TrackEnd:
        """Wait for the end; cancelling one wait never cancels the track."""
        return await asyncio.shield(self._entry.done)

    async def started(self) -> bool:
        """Wait until the track starts playing; False if it ended first."""
        return await asyncio.shield(self._entry.began)

    @property
    def position(self) -> float:
        """Seconds into the source that have played."""
        return self._entry.position()

    @property
    def passthrough(self) -> bool:
        sink = self._entry.sink
        current = self._current(self._entry)
        return sink is not None and current and sink.passthrough

    @property
    def paused(self) -> bool:
        return self._queue.paused and self._current(self._entry)

    @property
    def volume(self) -> float:
        return self._queue.volume

    @volume.setter
    def volume(self, volume: float) -> None:
        self._queue.volume = volume

    @property
    def filters(self) -> Filters:
        return self._queue.filters

    @filters.setter
    def filters(self, filters: Filters) -> None:
        self._queue.filters = filters

    def pause(self) -> None:
        """Stop feeding the sink; the audio it already holds still plays."""
        if self._current(self._entry):
            self._queue.pause()

    def resume(self) -> None:
        if self._current(self._entry):
            self._queue.resume()

    async def seek(self, position: float) -> None:
        await self._seek(self._entry, position)

    def stop(self) -> None:
        self._stop(self._entry)


class Queue:
    """Plays tracks back to back without gaps, or overlapping by `crossfade` seconds."""

    def __init__(
        self,
        vc: "VoiceConnection",
        *,
        volume: float = 1.0,
        filters: Filters = NO_FILTERS,
        crossfade: float | None = None,
        history: int | None = None,
        **options: Unpack[SinkOptions],
    ) -> None:
        check_keys(options, SINK_OPTIONS, "options")
        defaults = vc.manager.defaults
        self.vc = vc
        self.crossfade = check_crossfade(
            defaults.crossfade if crossfade is None else crossfade
        )
        if history is not None and history < 0:
            raise ValueError(f"history must be at least 0, got {history}")
        self._history: deque[Track] = deque(
            maxlen=defaults.history if history is None else history
        )
        self._volume = volume
        self._filters = filters
        self._options = options
        self._closed = False
        self._loop: Loop = None
        self._entries: list[_Entry] = []
        self._sink: Sink | None = None
        self._retiring: set[Sink] = set()
        self._format: Format | None = None
        self._gate = asyncio.Event()
        self._gate.set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._feeder: asyncio.Task[None] | None = None
        self._busy: _Entry | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def closed(self) -> bool:
        """Whether the connection ended; a closed queue takes no more tracks."""
        return self._closed

    @property
    def now(self) -> Track | None:
        if self._entries and self._entries[0].sink is not None:
            return self._entries[0].track
        return None

    @property
    def upcoming(self) -> tuple[Track, ...]:
        return tuple(entry.track for entry in self._upcoming())

    @property
    def history(self) -> tuple[Track, ...]:
        """The tracks that played, oldest first, as many as `history` keeps."""
        return tuple(self._history)

    @property
    def paused(self) -> bool:
        """Whether `pause()` holds the tracks back."""
        return not self._gate.is_set()

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, volume: float) -> None:
        self._volume = volume
        for sink in (*self._retiring, self._sink):
            if sink is not None:
                sink.volume = volume

    @property
    def filters(self) -> Filters:
        """The effects on every track: equalizer, bass, speed and pitch."""
        return self._filters

    @filters.setter
    def filters(self, filters: Filters) -> None:
        self._filters = filters
        for sink in (*self._retiring, self._sink):
            if sink is not None:
                sink.filters = filters

    @property
    def loop(self) -> Loop:
        """What plays again: the current "track", the whole "queue", or None."""
        return self._loop

    @loop.setter
    def loop(self, mode: Loop) -> None:
        if mode not in (None, "track", "queue"):
            raise ValueError(f"loop must be 'track', 'queue' or None, got {mode!r}")
        if mode == self._loop:
            return
        self._loop = mode
        for entry in self._entries:
            entry.repeated = False
        self._drop([entry for entry in self._upcoming() if entry.origin is not None])
        if mode == "track" and self.now is not None:
            self._repeat(self._entries[0])
        elif mode == "queue":
            for entry in [entry for entry in self._entries if entry.written]:
                self._repeat(entry)

    def add(
        self, source: "Source", *, meta: Any = None, index: int | None = None
    ) -> Track:
        """Queue a source at the end, or at `index` among the upcoming tracks."""
        return self._add(source, meta, index).track

    def move(self, track: Track, index: int) -> None:
        """Move an upcoming track to `index` among the upcoming tracks."""
        upcoming = self._upcoming()
        entry = next((entry for entry in upcoming if entry.track is track), None)
        if entry is None:
            raise ValueError("only an upcoming track of this queue can move")
        upcoming.remove(entry)
        upcoming.insert(index, entry)
        self._arrange(upcoming)

    def shuffle(self) -> None:
        """Put the upcoming tracks in a random order."""
        upcoming = self._upcoming()
        random.shuffle(upcoming)
        self._arrange(upcoming)

    def skip(self) -> None:
        """End the current track and go on with the next one."""
        if not self._entries:
            return
        if self._loop == "track":
            self._drop(self._repeats_of(self._entries[0]))
        head = self._entries[0]
        moved_on = head.stage is _Stage.WRITTEN
        sink = head.sink
        self._end(head, EndReason.STOPPED)
        if not moved_on:
            if sink is not None:
                sink.clear()
            self._restart()
        elif sink is not None:
            following = self._entries[0] if self._entries else None
            if (
                following is not None
                and following.start is not None
                and following.sink is sink
            ):
                sink.clear(until=following.start)
            else:
                sink.clear()
        self._settle()

    def back(self) -> Track | None:
        """Play the last track of the history again, then the current one anew.

        None when the history is empty; SourceError when that track cannot play again.
        """
        if not self._history:
            return None
        previous = self._history[-1]
        current = self._entries[0] if self.now is not None else None
        replay = self._insert(previous.source.again(), previous.meta, 0)
        self._history.pop()
        if current is not None:
            with contextlib.suppress(SourceError):
                self._insert(current.source.again(), current.meta, 1)
            self.skip()
            if self._history and self._history[-1] is current.track:
                self._history.pop()
        return replay

    def remove(self, track: Track) -> None:
        entry = next((entry for entry in self._entries if entry.track is track), None)
        if entry is not None:
            self._stop_track(entry)

    def clear(self) -> None:
        """End every upcoming track; the current one keeps playing."""
        first = 0 if self.now is None else 1
        self._drop(self._entries[first:])

    def pause(self) -> None:
        self._gate.clear()

    def resume(self) -> None:
        self._gate.set()

    def stop(self) -> None:
        """End every track and close the sink; later adds play again."""
        sink = self._sink
        for entry in tuple(self._entries):
            self._end(entry, EndReason.STOPPED)
        if sink is not None:
            sink.clear()
        for retiring in tuple(self._retiring):
            retiring.close()
        self._settle()

    async def join(self) -> None:
        """Wait until nothing is queued or playing."""
        await self._idle.wait()

    def _add(self, source: "Source", meta: Any, index: int | None) -> _Entry:
        if self._closed or self.vc.state is State.DISCONNECTED:
            self._lost()
            raise VoiceNotConnected("the voice connection has ended")
        ready = not self._entries and source.opened and not self.crossfade
        sink = self._sink_for(source.format) if ready else None
        entry = self._enlist(source, meta)
        if sink is not None:
            entry.stage, entry.sink = _Stage.OPEN, sink
        if index is not None:
            upcoming = self._upcoming()
            upcoming.remove(entry)
            upcoming.insert(index, entry)
            self._arrange(upcoming)
        elif self._feeder is not None:
            self._open_ahead()
        if self._feeder is None:
            self._feeder = asyncio.create_task(self._feed(None))
        return entry

    def _insert(self, source: "Source", meta: Any, index: int) -> Track:
        entry = self._add(source, meta, index)
        entry.repeated = self._loop == "queue"
        return entry.track

    def _is_current(self, entry: _Entry) -> bool:
        return bool(self._entries) and self._entries[0] is entry

    def _stop_track(self, entry: _Entry) -> None:
        if entry.stage is _Stage.ENDED:
            return
        if self._is_current(entry):
            self.skip()
        else:
            self._drop([entry])

    async def _seek(self, entry: _Entry, position: float) -> None:
        if not self._is_current(entry):
            raise StateError("only the current track can seek")
        if not entry.source.seekable:
            raise SourceError(repr(entry.source), "not seekable")
        previous = self._feeder
        if previous is not None:
            previous.cancel()
        moving = asyncio.create_task(self._reposition(previous, entry, position))
        self._feeder = asyncio.create_task(self._feed(moving))
        for other in self._entries:
            other.unwrite()
        entry.base = position
        entry.exhausted = False
        for sink in {entry.sink, self._sink}:
            if sink is not None:
                sink.clear()
        try:
            await asyncio.shield(moving)
        except Exception as error:
            self._end(entry, EndReason.ERROR, error)
            self._restart()
            raise

    def _enlist(self, source: "Source", meta: Any) -> _Entry:
        loop = asyncio.get_running_loop()
        entry = _Entry(source, meta, loop.create_future(), loop.create_future())
        entry.track = Track(
            self,
            entry,
            current=self._is_current,
            seek=self._seek,
            stop=self._stop_track,
        )
        self._entries.append(entry)
        self._idle.clear()
        return entry

    def _repeats_of(self, entry: _Entry) -> list[_Entry]:
        family = {id(entry)}
        repeats: list[_Entry] = []
        for candidate in self._entries:
            if candidate.origin is not None and id(candidate.origin) in family:
                family.add(id(candidate))
                repeats.append(candidate)
        return repeats

    def _repeat(self, entry: _Entry) -> None:
        if self._loop is None or entry.repeated or entry.stage is _Stage.ENDED:
            return
        try:
            source = entry.source.again()
        except SourceError:
            return
        entry.repeated = True
        copy = self._enlist(source, entry.meta)
        copy.origin = entry
        upcoming = self._upcoming()
        if self._loop == "track":
            upcoming.remove(copy)
            upcoming.insert(upcoming.index(entry) + 1 if entry in upcoming else 0, copy)
        self._arrange(upcoming)

    def _upcoming(self) -> list[_Entry]:
        return self._entries[0 if self.now is None else 1 :]

    def _arrange(self, upcoming: list[_Entry]) -> None:
        first = len(self._entries) - len(upcoming)
        pending = self._pending()
        written = [entry for entry in self._entries[first:] if entry.written]
        self._entries[first:] = upcoming
        if self._feeder is None:
            return
        if upcoming[: len(written)] != written or self._pending() is not pending:
            if written:
                self._unwrite()
            self._restart()
        else:
            self._open_ahead()

    def _unwrite(self) -> None:
        head = self._entries[0] if self._entries else None
        for entry in self._entries[1:]:
            entry.unwrite()
        sink = self._sink
        if sink is None:
            return
        if head is not None and head.end is not None and head.sink is sink:
            sink.clear(since=head.end)
        else:
            sink.clear()

    def _pending(self) -> _Entry | None:
        return next(
            (entry for entry in self._entries if entry.stage is not _Stage.WRITTEN),
            None,
        )

    def _drop(self, entries: list[_Entry]) -> None:
        if not entries:
            return
        pending = self._pending()
        written = any(entry.written for entry in entries)
        for entry in entries:
            self._end(entry, EndReason.STOPPED)
        if written:
            self._unwrite()
        if written or pending in entries:
            self._restart()
        else:
            self._open_ahead()
        self._settle()

    def _restart(self) -> None:
        previous = self._feeder
        if previous is not None:
            previous.cancel()
        self._feeder = (
            asyncio.create_task(self._feed(previous)) if self._entries else None
        )

    def _open_ahead(self) -> None:
        current = self._pending()
        if current is None or current.stage is not _Stage.WRITING:
            return
        following = self._entries.index(current) + 1
        if following < len(self._entries):
            upcoming = self._entries[following]
            if upcoming.stage is _Stage.QUEUED:
                self._start_opening(upcoming)

    def _start_opening(self, entry: _Entry) -> None:
        entry.opening = asyncio.create_task(self._open(entry.source))
        entry.stage = _Stage.OPENING

    async def _open(self, source: "Source") -> Exception | None:
        try:
            await source.open(self.vc.manager)
        except Exception as error:
            return error
        return None

    async def _reposition(
        self, previous: asyncio.Task[None] | None, entry: _Entry, position: float
    ) -> None:
        if previous is not None:
            await asyncio.wait({previous})
        await entry.source.seek(position)

    async def _feed(self, previous: asyncio.Task[None] | None) -> None:
        if previous is not None:
            await asyncio.wait({previous})
        try:
            await self._feed_all()
        except (SinkClosed, VoiceNotConnected):
            self._feeder = None
            self._lost()
            return
        self._feeder = None
        self._settle()

    async def _feed_all(self) -> None:
        drained = False
        while True:
            entry = self._pending()
            if entry is not None:
                drained = False
                await self._feed_entry(entry)
            elif drained or self._sink is None:
                return
            else:
                await self._drain(self._sink)
                drained = True

    async def _feed_entry(self, entry: _Entry) -> None:
        try:
            await self._write(entry)
        except (SinkClosed, VoiceNotConnected):
            raise
        except Exception as error:
            if self._sink is not None and self._sink.closed:
                raise SinkClosed("the sink is closed") from error
            self._end(entry, EndReason.ERROR, error)

    async def _write(self, entry: _Entry) -> None:
        if entry.stage is _Stage.QUEUED:
            self._start_opening(entry)
        if entry.stage is _Stage.OPENING and entry.opening is not None:
            error = await asyncio.shield(entry.opening)
            if error is not None:
                raise error
            entry.stage = _Stage.OPEN
        sink = self._sink_for(entry.source.format)
        entry.sink = sink
        entry.start = sink.mark()
        entry.stage = _Stage.WRITING
        spawn(self._tasks, self._begin(entry, sink, entry.start))
        self._repeat(entry)
        self._open_ahead()
        for chunk in tuple(entry.kept):
            await self._gate.wait()
            await sink.write(chunk)
        if not entry.exhausted:
            self._busy = entry
            try:
                async for chunk in entry.source:
                    if not self._is_current(entry):
                        entry.kept.append(chunk)
                    await self._gate.wait()
                    await sink.write(chunk)
            finally:
                self._busy = None
            entry.exhausted = True
        entry.end = sink.mark()
        entry.stage = _Stage.WRITTEN
        spawn(self._tasks, self._finish(entry, entry.end))

    def _sink_for(self, format: Format) -> "Sink":
        if self._sink is not None and format == self._format and not self.crossfade:
            return self._sink
        previous = self._sink
        self._sink = self.vc.sink(
            format,
            volume=self._volume,
            filters=self._filters,
            after=previous,
            crossfade=self.crossfade,
            **self._options,
        )
        self._format = format
        if previous is not None:
            self._retiring.add(previous)
            spawn(self._tasks, self._retire(previous))
        return self._sink

    async def _retire(self, sink: "Sink") -> None:
        try:
            with contextlib.suppress(SinkClosed):
                await sink.drain()
        finally:
            sink.close()
            self._retiring.discard(sink)

    async def _drain(self, sink: "Sink") -> None:
        if not await sink.drain() and sink.closed:
            raise SinkClosed("the sink is closed")

    async def _begin(self, entry: _Entry, sink: "Sink", marker: "Marker") -> None:
        await marker
        if entry.start is marker and not sink.closed and not entry.began.done():
            entry.began.set_result(True)

    async def _finish(self, entry: _Entry, marker: "Marker") -> None:
        played = await marker
        if entry.end is not marker or entry.stage is _Stage.ENDED:
            return
        sink = entry.sink
        if not played and sink is not None and sink.closed:
            self._lost()
            return
        self._end(entry, EndReason.FINISHED)
        self._settle()

    def _end(
        self, entry: _Entry, reason: EndReason, error: BaseException | None = None
    ) -> None:
        if entry.stage is _Stage.ENDED:
            return
        entry.final = entry.position()
        entry.stage = _Stage.ENDED
        blocker = self._feeder if self._busy is entry else None
        self._entries.remove(entry)
        if self._entries:
            self._entries[0].kept.clear()
        may_start_next = reason in (EndReason.FINISHED, EndReason.ERROR)
        if not entry.began.done():
            entry.began.set_result(reason is EndReason.FINISHED)
        if entry.began.result():
            self._history.append(entry.track)
        entry.done.set_result(TrackEnd(entry.track, reason, error, may_start_next))
        spawn(self._tasks, self._release(entry, blocker), eager=True)

    async def _release(self, entry: _Entry, blocker: asyncio.Task[None] | None) -> None:
        if blocker is not None:
            await asyncio.wait({blocker})
        if entry.opening is not None:
            entry.opening.cancel()
            await asyncio.wait({entry.opening})
        try:
            await entry.source.aclose()
        except Exception:
            log.exception("closing %r failed", entry.source)

    def _lost(self) -> None:
        ended = self.vc.state is State.DISCONNECTED
        self._closed = self._closed or ended
        reason = EndReason.CLEANUP if ended else EndReason.STOPPED
        for entry in tuple(self._entries):
            self._end(entry, reason)
        self._sink = None
        self._settle()

    def _settle(self) -> None:
        if self._entries:
            return
        if self._feeder is not None:
            self._feeder.cancel()
            self._feeder = None
        if self._sink is not None:
            self._sink.close()
            self._sink = None
        self._idle.set()


async def play(
    vc: "VoiceConnection",
    source: "Source",
    *,
    volume: float = 1.0,
    filters: Filters = NO_FILTERS,
    meta: Any = None,
    **options: Unpack[SinkOptions],
) -> Track:
    queue = Queue(
        vc, volume=volume, filters=filters, crossfade=0.0, history=0, **options
    )
    try:
        await source.open(vc.manager)
        return queue.add(source, meta=meta)
    except BaseException:
        await source.aclose()
        raise


async def broadcast(
    source: "Source",
    connections: Iterable["VoiceConnection"],
    *,
    encoder: EncoderSettings = DEFAULT_ENCODER,
    stall: SinkStall = "drop",
) -> None:
    """Play one source on every given connection, encoding PCM once.

    Connections that join later are not added; one that ends leaves the set.
    """
    sinks: list[Sink] = []
    try:
        targets = tuple(connections)
        if not targets:
            return
        await source.open(targets[0].manager)
        sinks = [
            vc.sink(Opus(), stall=stall)
            for vc in targets
            if vc.state is not State.DISCONNECTED
        ]
        format = source.format
        opus = OpusEncoder(format, encoder=encoder) if isinstance(format, PCM) else None
        async for chunk in source:
            for packet in [chunk] if opus is None else opus.encode(chunk):
                sinks = await _write_all(sinks, packet)
            if not sinks:
                return
        for packet in [] if opus is None else opus.flush():
            sinks = await _write_all(sinks, packet)
        await asyncio.gather(*(sink.drain() for sink in sinks))
    finally:
        for sink in sinks:
            sink.close()
        await source.aclose()


async def _write_all(sinks: list["Sink"], packet: bytes) -> list["Sink"]:
    results = await asyncio.gather(
        *(sink.write(packet) for sink in sinks), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, SinkClosed):
            raise result
    return [sink for sink, result in zip(sinks, results, strict=True) if result is None]
