import asyncio
import logging
from collections.abc import Coroutine, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from bostie.enums import State
from bostie.errors import SinkClosed, SourceError, VoiceNotConnected
from bostie.voice.encoder import OpusEncoder
from bostie.voice.formats import PCM, Encoder, Format, Opus

if TYPE_CHECKING:
    from bostie.voice.connection import VoiceConnection
    from bostie.voice.sink import Marker, Sink
    from bostie.voice.sources import Source

log = logging.getLogger("bostie.voice")

BROADCAST_BITRATE = 64_000


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


@dataclass(eq=False, slots=True)
class _Entry:
    source: "Source"
    meta: Any
    done: asyncio.Future[TrackEnd]
    track: "Track" = field(init=False)
    opened: bool = False
    opening: asyncio.Task[Exception | None] | None = None
    sink: "Sink | None" = None
    start: "Marker | None" = None
    end: "Marker | None" = None
    base: float = 0.0
    exhausted: bool = False
    kept: list[bytes] = field(default_factory=list)
    final: float | None = None

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

    def __init__(self, player: "_Player", entry: _Entry) -> None:
        self.source = entry.source
        self.meta = entry.meta
        self._player = player
        self._entry = entry

    @property
    def sink(self) -> "Sink":
        """The sink this track is written to; RuntimeError until it starts."""
        if self._entry.sink is None:
            raise RuntimeError("the track has not started")
        return self._entry.sink

    @property
    def ended(self) -> TrackEnd | None:
        done = self._entry.done
        return done.result() if done.done() else None

    async def wait(self) -> TrackEnd:
        """Wait for the end; cancelling one wait never cancels the track."""
        return await asyncio.shield(self._entry.done)

    @property
    def position(self) -> float:
        """Seconds into the source that have played."""
        return self._entry.position()

    @property
    def passthrough(self) -> bool:
        sink = self._entry.sink
        current = self._player.is_current(self._entry)
        return sink is not None and current and sink.passthrough

    @property
    def paused(self) -> bool:
        return self._player.paused and self._player.is_current(self._entry)

    @property
    def volume(self) -> float:
        return self._player.volume

    @volume.setter
    def volume(self, volume: float) -> None:
        self._player.volume = volume

    def pause(self) -> None:
        """Stop feeding the sink; the audio it already holds still plays."""
        if self._player.is_current(self._entry):
            self._player.pause()

    def resume(self) -> None:
        if self._player.is_current(self._entry):
            self._player.resume()

    async def seek(self, position: float) -> None:
        await self._player.seek(self._entry, position)

    def stop(self) -> None:
        self._player.stop_track(self._entry)


class _Player:
    def __init__(
        self,
        vc: "VoiceConnection",
        *,
        volume: float,
        buffer: float | None,
        stall: Literal["hold", "drop"] | None,
    ) -> None:
        self.vc = vc
        self.closed = False
        self._volume = volume
        self._buffer = buffer
        self._stall = stall
        self._entries: list[_Entry] = []
        self._sink: Sink | None = None
        self._format: Format | None = None
        self._gate = asyncio.Event()
        self._gate.set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._feeder: asyncio.Task[None] | None = None
        self._busy: _Entry | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def now(self) -> Track | None:
        if self._entries and self._entries[0].sink is not None:
            return self._entries[0].track
        return None

    @property
    def upcoming(self) -> tuple[Track, ...]:
        first = 0 if self.now is None else 1
        return tuple(entry.track for entry in self._entries[first:])

    @property
    def paused(self) -> bool:
        return not self._gate.is_set()

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, volume: float) -> None:
        self._volume = volume
        if self._sink is not None:
            self._sink.volume = volume

    def is_current(self, entry: _Entry) -> bool:
        return bool(self._entries) and self._entries[0] is entry

    def start(self, source: "Source", sink: "Sink", meta: Any) -> Track:
        self._sink = sink
        self._format = source.format
        entry = self._enlist(source, meta)
        entry.opened = True
        entry.sink = sink
        self._feeder = asyncio.create_task(self._feed(None))
        return entry.track

    def add(self, source: "Source", meta: Any) -> Track:
        if self.closed or self.vc.state is State.DISCONNECTED:
            self._cleanup()
            raise VoiceNotConnected("the voice connection has ended")
        entry = self._enlist(source, meta)
        if self._feeder is None:
            self._feeder = asyncio.create_task(self._feed(None))
        else:
            self._open_ahead()
        return entry.track

    def skip(self) -> None:
        if not self._entries:
            return
        head = self._entries[0]
        moved_on = head.end is not None
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

    def remove(self, track: Track) -> None:
        entry = next((entry for entry in self._entries if entry.track is track), None)
        if entry is not None:
            self.stop_track(entry)

    def clear(self) -> None:
        first = 0 if self.now is None else 1
        self._drop(self._entries[first:])

    def stop_track(self, entry: _Entry) -> None:
        if entry.done.done():
            return
        if self.is_current(entry):
            self.skip()
        else:
            self._drop([entry])

    def pause(self) -> None:
        self._gate.clear()

    def resume(self) -> None:
        self._gate.set()

    def stop(self) -> None:
        sink = self._sink
        for entry in tuple(self._entries):
            self._end(entry, EndReason.STOPPED)
        if sink is not None:
            sink.clear()
        self._settle()

    async def join(self) -> None:
        await self._idle.wait()

    async def seek(self, entry: _Entry, position: float) -> None:
        if not self.is_current(entry):
            raise RuntimeError("only the current track can seek")
        if not entry.source.seekable:
            raise SourceError(repr(entry.source), "not seekable")
        previous = self._feeder
        if previous is not None:
            previous.cancel()
        moving = asyncio.create_task(self._reposition(previous, entry, position))
        self._feeder = asyncio.create_task(self._feed(moving))
        for other in self._entries:
            other.start = other.end = None
        entry.base = position
        entry.exhausted = False
        if entry.sink is not None:
            entry.sink.clear()
        try:
            await asyncio.shield(moving)
        except Exception as error:
            self._end(entry, EndReason.ERROR, error)
            self._restart()
            raise

    def _enlist(self, source: "Source", meta: Any) -> _Entry:
        entry = _Entry(source, meta, asyncio.get_running_loop().create_future())
        entry.track = Track(self, entry)
        self._entries.append(entry)
        self._idle.clear()
        return entry

    def _pending(self) -> _Entry | None:
        return next((entry for entry in self._entries if entry.end is None), None)

    def _drop(self, entries: list[_Entry]) -> None:
        if not entries:
            return
        pending = self._pending()
        written = any(entry.start is not None for entry in entries)
        for entry in entries:
            self._end(entry, EndReason.STOPPED)
        if written:
            for entry in self._entries[1:]:
                entry.start = entry.end = None
            if self._sink is not None:
                self._sink.clear()
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
        if current is None or current.start is None:
            return
        following = self._entries.index(current) + 1
        if following < len(self._entries) and not self._entries[following].opened:
            self._opening(self._entries[following])

    def _opening(self, entry: _Entry) -> asyncio.Task[Exception | None]:
        if entry.opening is None:
            entry.opening = asyncio.create_task(self._open(entry.source))
        return entry.opening

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
            self._cleanup()
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
        if not entry.opened:
            error = await asyncio.shield(self._opening(entry))
            if error is not None:
                raise error
            entry.opened = True
        sink = await self._sink_for(entry.source.format)
        entry.sink = sink
        entry.start = sink.mark()
        self._open_ahead()
        for chunk in tuple(entry.kept):
            await self._gate.wait()
            await sink.write(chunk)
        if not entry.exhausted:
            self._busy = entry
            try:
                async for chunk in entry.source:
                    if not self.is_current(entry):
                        entry.kept.append(chunk)
                    await self._gate.wait()
                    await sink.write(chunk)
            finally:
                self._busy = None
            entry.exhausted = True
        entry.end = sink.mark()
        task = asyncio.create_task(self._finish(entry, entry.end))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _sink_for(self, format: Format) -> "Sink":
        if self._sink is not None and format == self._format:
            return self._sink
        if self._sink is not None:
            previous = self._sink
            await self._drain(previous)
            self._sink = None
            previous.close()
        self._sink = self.vc.sink(
            format, buffer=self._buffer, volume=self._volume, stall=self._stall
        )
        self._format = format
        return self._sink

    async def _drain(self, sink: "Sink") -> None:
        if not await sink.drain() and sink.closed:
            raise SinkClosed("the sink is closed")

    async def _finish(self, entry: _Entry, marker: "Marker") -> None:
        await marker
        if entry.end is not marker or entry.done.done():
            return
        sink = entry.sink
        if sink is not None and sink is self._sink and sink.closed:
            self._cleanup()
            return
        self._end(entry, EndReason.FINISHED)
        self._settle()

    def _end(
        self, entry: _Entry, reason: EndReason, error: BaseException | None = None
    ) -> None:
        if entry.done.done():
            return
        entry.final = entry.position()
        blocker = self._feeder if self._busy is entry else None
        self._entries.remove(entry)
        if self._entries:
            self._entries[0].kept.clear()
        may_start_next = reason in (EndReason.FINISHED, EndReason.ERROR)
        entry.done.set_result(TrackEnd(entry.track, reason, error, may_start_next))
        _spawn(self._tasks, self._release(entry, blocker))

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

    def _cleanup(self) -> None:
        self.closed = True
        for entry in tuple(self._entries):
            self._end(entry, EndReason.CLEANUP)
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


class Queue:
    """Plays tracks back to back, gapless while consecutive formats match."""

    def __init__(
        self,
        vc: "VoiceConnection",
        *,
        volume: float = 1.0,
        buffer: float | None = None,
        stall: Literal["hold", "drop"] | None = None,
    ) -> None:
        self.vc = vc
        self._player = _Player(vc, volume=volume, buffer=buffer, stall=stall)

    @property
    def closed(self) -> bool:
        """Whether the connection ended; a closed queue takes no more tracks."""
        return self._player.closed

    def add(self, source: "Source", *, meta: Any = None) -> Track:
        return self._player.add(source, meta)

    @property
    def now(self) -> Track | None:
        return self._player.now

    @property
    def upcoming(self) -> tuple[Track, ...]:
        return self._player.upcoming

    @property
    def volume(self) -> float:
        return self._player.volume

    @volume.setter
    def volume(self, volume: float) -> None:
        self._player.volume = volume

    def skip(self) -> None:
        """End the current track and go on with the next one."""
        self._player.skip()

    def remove(self, track: Track) -> None:
        self._player.remove(track)

    def clear(self) -> None:
        """End every upcoming track; the current one keeps playing."""
        self._player.clear()

    def pause(self) -> None:
        self._player.pause()

    def resume(self) -> None:
        self._player.resume()

    def stop(self) -> None:
        """End every track and close the sink; later adds play again."""
        self._player.stop()

    async def join(self) -> None:
        """Wait until nothing is queued or playing."""
        await self._player.join()


async def play(
    vc: "VoiceConnection",
    source: "Source",
    *,
    volume: float = 1.0,
    buffer: float | None = None,
    stall: Literal["hold", "drop"] | None = None,
    meta: Any = None,
) -> Track:
    try:
        await source.open(vc.manager)
        sink = vc.sink(source.format, buffer=buffer, volume=volume, stall=stall)
    except BaseException:
        await source.aclose()
        raise
    player = _Player(vc, volume=volume, buffer=buffer, stall=stall)
    return player.start(source, sink, meta)


async def broadcast(
    source: "Source",
    connections: Iterable["VoiceConnection"],
    *,
    encoder: Encoder | None = None,
    stall: Literal["hold", "drop"] = "drop",
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
        settings = Encoder(bitrate=BROADCAST_BITRATE) if encoder is None else encoder
        format = source.format
        opus = (
            OpusEncoder(format, encoder=settings) if isinstance(format, PCM) else None
        )
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


def _spawn(
    tasks: set[asyncio.Task[None]], coroutine: Coroutine[Any, Any, None]
) -> None:
    task = asyncio.Task(coroutine, loop=asyncio.get_running_loop(), eager_start=True)
    if not task.done():
        tasks.add(task)
        task.add_done_callback(tasks.discard)
