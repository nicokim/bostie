import asyncio
import contextlib
import logging
import math
import os
import random
import string
import struct
import time
import wave
import zlib
from collections import deque
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, Self, Unpack

from bostie._aio import spawn
from bostie.enums import State
from bostie.errors import (
    Empty,
    ReceiverClosed,
    SinkClosed,
    SourceError,
    StateError,
    VoiceNotConnected,
)
from bostie.models.base import check_keys
from bostie.voice.encoder import DEFAULT_ENCODER, OpusEncoder
from bostie.voice.events import SpeechStart, SpeechStop, UserLeave
from bostie.voice.formats import (
    DEFAULT_PCM,
    NO_FILTERS,
    PCM,
    SAMPLE_BYTES,
    SAMPLE_RATE,
    SINK_OPTIONS,
    EncoderSettings,
    Filters,
    Format,
    Opus,
    SinkOptions,
    SinkStall,
)
from bostie.voice.frames import Frame

if TYPE_CHECKING:
    from bostie.voice.connection import VoiceConnection
    from bostie.voice.receiver import Receiver, ReceiverStats
    from bostie.voice.sink import Marker, Sink
    from bostie.voice.sources import Source

log = logging.getLogger("bostie.voice")

SILENCE = b"\xf8\xff\xfe"
SILENCE_SAMPLES = 960
PAGE_SAMPLES = SAMPLE_RATE
MAX_SEGMENTS = 255
MAX_PACKET = MAX_SEGMENTS * 255 - 1
BEGINNING_OF_STREAM = 0x02
END_OF_STREAM = 0x04
OPUS_HEAD = struct.pack("<8sBBHIhB", b"OpusHead", 1, 2, 0, SAMPLE_RATE, 0, 0)
OPUS_TAGS = struct.pack("<8sI6sI", b"OpusTags", 6, b"bostie", 0)
REVERSED_BITS = bytes(int(f"{value:08b}"[::-1], 2) for value in range(256))
TEMPLATE_FIELDS = frozenset({"guild_id", "user_id"})

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


@dataclass(eq=False, slots=True)
class _Entry:
    source: "Source"
    meta: Any
    done: asyncio.Future[TrackEnd]
    began: asyncio.Future[bool]
    track: "Track" = field(init=False)
    opened: bool = False
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

    @property
    def filters(self) -> Filters:
        return self._player.filters

    @filters.setter
    def filters(self, filters: Filters) -> None:
        self._player.filters = filters

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
        options: SinkOptions,
        filters: Filters = NO_FILTERS,
        crossfade: float = 0.0,
        history: int = 0,
    ) -> None:
        self.vc = vc
        self.closed = False
        self._filters = filters
        self.history: deque[Track] = deque(maxlen=history)
        self._volume = volume
        self._options = options
        self._crossfade = crossfade
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
    def now(self) -> Track | None:
        if self._entries and self._entries[0].sink is not None:
            return self._entries[0].track
        return None

    @property
    def upcoming(self) -> tuple[Track, ...]:
        return tuple(entry.track for entry in self._upcoming())

    @property
    def paused(self) -> bool:
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
        return self._filters

    @filters.setter
    def filters(self, filters: Filters) -> None:
        self._filters = filters
        for sink in (*self._retiring, self._sink):
            if sink is not None:
                sink.filters = filters

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

    def add(self, source: "Source", meta: Any, index: int | None) -> Track:
        return self._add(source, meta, index).track

    def _add(self, source: "Source", meta: Any, index: int | None) -> _Entry:
        if self.closed or self.vc.state is State.DISCONNECTED:
            self._lost()
            raise VoiceNotConnected("the voice connection has ended")
        entry = self._enlist(source, meta)
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

    def move(self, track: Track, index: int) -> None:
        upcoming = self._upcoming()
        entry = next((entry for entry in upcoming if entry.track is track), None)
        if entry is None:
            raise ValueError("only an upcoming track of this queue can move")
        upcoming.remove(entry)
        upcoming.insert(index, entry)
        self._arrange(upcoming)

    def shuffle(self) -> None:
        upcoming = self._upcoming()
        random.shuffle(upcoming)
        self._arrange(upcoming)

    @property
    def loop(self) -> Loop:
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
        if mode is not None and self.now is not None:
            self._repeat(self._entries[0])

    def skip(self) -> None:
        if not self._entries:
            return
        if self._loop == "track":
            self._drop(self._repeats_of(self._entries[0]))
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

    def back(self) -> Track | None:
        if not self.history:
            return None
        previous = self.history[-1]
        current = self._entries[0] if self.now is not None else None
        replay = self._insert(previous.source.again(), previous.meta, 0)
        self.history.pop()
        if current is not None:
            with contextlib.suppress(SourceError):
                self._insert(current.source.again(), current.meta, 1)
            self.skip()
            if self.history and self.history[-1] is current.track:
                self.history.pop()
        return replay

    def _insert(self, source: "Source", meta: Any, index: int) -> Track:
        entry = self._add(source, meta, index)
        entry.repeated = self._loop == "queue"
        return entry.track

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
        for retiring in tuple(self._retiring):
            retiring.close()
        self._settle()

    async def join(self) -> None:
        await self._idle.wait()

    async def seek(self, entry: _Entry, position: float) -> None:
        if not self.is_current(entry):
            raise StateError("only the current track can seek")
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
        entry.track = Track(self, entry)
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
        if self._loop is None or entry.repeated or entry.done.done():
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
        written = [entry for entry in self._entries[first:] if entry.start is not None]
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
            entry.start = entry.end = None
        sink = self._sink
        if sink is None:
            return
        if head is not None and head.end is not None and head.sink is sink:
            sink.clear(since=head.end)
        else:
            sink.clear()

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
        if current is None or current.start is None:
            return
        following = self._entries.index(current) + 1
        if following < len(self._entries) and not self._entries[following].opened:
            _ = self._opening(self._entries[following])

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
        if not entry.opened:
            error = await asyncio.shield(self._opening(entry))
            if error is not None:
                raise error
            entry.opened = True
        sink = self._sink_for(entry.source.format)
        entry.sink = sink
        entry.start = sink.mark()
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
                    if not self.is_current(entry):
                        entry.kept.append(chunk)
                    await self._gate.wait()
                    await sink.write(chunk)
            finally:
                self._busy = None
            entry.exhausted = True
        entry.end = sink.mark()
        spawn(self._tasks, self._finish(entry, entry.end))

    def _sink_for(self, format: Format) -> "Sink":
        if self._sink is not None and format == self._format and not self._crossfade:
            return self._sink
        previous = self._sink
        self._sink = self.vc.sink(
            format,
            volume=self._volume,
            filters=self._filters,
            after=previous,
            crossfade=self._crossfade,
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
        if entry.end is not marker or entry.done.done():
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
        if entry.done.done():
            return
        entry.final = entry.position()
        blocker = self._feeder if self._busy is entry else None
        self._entries.remove(entry)
        if self._entries:
            self._entries[0].kept.clear()
        may_start_next = reason in (EndReason.FINISHED, EndReason.ERROR)
        if not entry.began.done():
            entry.began.set_result(reason is EndReason.FINISHED)
        if entry.began.result():
            self.history.append(entry.track)
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
        self.closed = self.closed or ended
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
        self.crossfade = defaults.crossfade if crossfade is None else crossfade
        if history is not None and history < 0:
            raise ValueError(f"history must be at least 0, got {history}")
        self._player = _Player(
            vc,
            volume=volume,
            options=options,
            filters=filters,
            crossfade=self.crossfade,
            history=defaults.history if history is None else history,
        )

    @property
    def closed(self) -> bool:
        """Whether the connection ended; a closed queue takes no more tracks."""
        return self._player.closed

    def add(
        self, source: "Source", *, meta: Any = None, index: int | None = None
    ) -> Track:
        """Queue a source at the end, or at `index` among the upcoming tracks."""
        return self._player.add(source, meta, index)

    def move(self, track: Track, index: int) -> None:
        """Move an upcoming track to `index` among the upcoming tracks."""
        self._player.move(track, index)

    def shuffle(self) -> None:
        """Put the upcoming tracks in a random order."""
        self._player.shuffle()

    @property
    def loop(self) -> Loop:
        """What plays again: the current "track", the whole "queue", or None."""
        return self._player.loop

    @loop.setter
    def loop(self, mode: Loop) -> None:
        self._player.loop = mode

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

    @property
    def filters(self) -> Filters:
        """The effects on every track: equalizer, bass, speed and pitch."""
        return self._player.filters

    @filters.setter
    def filters(self, filters: Filters) -> None:
        self._player.filters = filters

    @property
    def history(self) -> tuple[Track, ...]:
        """The tracks that played, oldest first, as many as `history` keeps."""
        return tuple(self._player.history)

    def skip(self) -> None:
        """End the current track and go on with the next one."""
        self._player.skip()

    def back(self) -> Track | None:
        """Play the last track of the history again, then the current one anew.

        None when the history is empty; SourceError when that track cannot play again.
        """
        return self._player.back()

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
    filters: Filters = NO_FILTERS,
    meta: Any = None,
    **options: Unpack[SinkOptions],
) -> Track:
    check_keys(options, SINK_OPTIONS, "options")
    try:
        await source.open(vc.manager)
        sink = vc.sink(source.format, volume=volume, filters=filters, **options)
    except BaseException:
        await source.aclose()
        raise
    player = _Player(vc, volume=volume, options=options, filters=filters)
    return player.start(source, sink, meta)


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


@dataclass(frozen=True, slots=True)
class Utterance:
    """One user's speech from start to end, in the receiver's PCM format."""

    user_id: int
    data: bytes
    offset: int
    duration: float
    lost: float


@dataclass(slots=True)
class _Speech:
    offset: int
    end: int
    data: bytearray = field(default_factory=bytearray)
    lost: int = 0
    stop: int | None = None
    deadline: float = math.inf

    def utterance(self, user_id: int) -> Utterance:
        return Utterance(
            user_id,
            bytes(self.data),
            self.offset,
            (self.end - self.offset) / SAMPLE_RATE,
            self.lost / SAMPLE_RATE,
        )


def utterances(
    rx: "Receiver[Frame | SpeechStart | SpeechStop | UserLeave]",
    *,
    min_silence: float = 0.8,
    max_length: float = 30.0,
    level: float | None = None,
) -> AsyncIterator[Utterance]:
    """Group a receiver's frames into utterances per user.

    With `level`, speech is audio louder than `level` dBFS, not the speech markers.
    """
    format = rx.format
    if not isinstance(format, PCM) or rx.mix or not (rx.markers or level is not None):
        raise ValueError(
            "utterances needs a PCM receiver with no mix, opened with markers=True "
            "or given a level"
        )
    grouping = _Grouping(
        silence=round(min_silence * SAMPLE_RATE),
        longest=round(max_length * SAMPLE_RATE),
        pause=rx.jitter + min_silence,
        width=format.channels * SAMPLE_BYTES[format.sample],
        rate=format.rate,
        level=level,
    )
    return _utterances(rx, grouping)


async def _utterances(
    rx: "Receiver[Frame | SpeechStart | SpeechStop | UserLeave]", grouping: "_Grouping"
) -> AsyncIterator[Utterance]:
    while True:
        deadline = grouping.deadline()
        try:
            item = await _next_before(rx, deadline)
        except StopAsyncIteration:
            break
        for utterance in grouping.take(item, deadline):
            yield utterance
    for user, speech in grouping.speaking.items():
        yield speech.utterance(user)


async def _next_before(
    rx: "Receiver[Frame | SpeechStart | SpeechStop | UserLeave]", deadline: float
) -> Frame | SpeechStart | SpeechStop | UserLeave | None:
    try:
        async with asyncio.timeout(
            None if math.isinf(deadline) else deadline - time.monotonic()
        ):
            return await anext(rx)
    except TimeoutError:
        return None


@dataclass(slots=True)
class _Grouping:
    silence: int
    longest: int
    pause: float
    width: int
    rate: int
    level: float | None
    speaking: dict[int, _Speech] = field(default_factory=dict[int, _Speech])

    def deadline(self) -> float:
        return min(
            (speech.deadline for speech in self.speaking.values()), default=math.inf
        )

    def take(
        self, item: Frame | SpeechStart | SpeechStop | UserLeave | None, deadline: float
    ) -> list[Utterance]:
        match item:
            case None:
                return self._expire(deadline)
            case Frame(user_id=int(user)) if self._quiet(item):
                return self._pause(user, item)
            case Frame(user_id=int(user)):
                return self._add(user, item)
            case UserLeave(user_id=user) if user in self.speaking:
                return [self.speaking.pop(user).utterance(user)]
            case _ if self.level is not None:
                pass
            case SpeechStart(user_id=user, offset=int(offset)):
                return self._start(user, offset)
            case SpeechStop(user_id=user, offset=int(offset), at=at):
                self._stop(user, offset, at)
            case _:
                pass
        return []

    def _quiet(self, frame: Frame) -> bool:
        return self.level is not None and (
            frame.level is None or frame.level < self.level
        )

    def _pause(self, user: int, frame: Frame) -> list[Utterance]:
        speech = self.speaking.get(user)
        if speech is None or frame.offset + frame.samples - speech.end < self.silence:
            return []
        return [self.speaking.pop(user).utterance(user)]

    def _expire(self, deadline: float) -> list[Utterance]:
        expired = [
            user
            for user, speech in self.speaking.items()
            if speech.deadline <= deadline
        ]
        return [self.speaking.pop(user).utterance(user) for user in expired]

    def _start(self, user: int, offset: int) -> list[Utterance]:
        speech = self.speaking.get(user)
        if (
            speech is not None
            and speech.stop is not None
            and offset - speech.stop < self.silence
        ):
            speech.stop, speech.deadline = None, math.inf
            return []
        self.speaking[user] = _Speech(offset, offset)
        return [] if speech is None else [speech.utterance(user)]

    def _stop(self, user: int, offset: int, at: float) -> None:
        speech = self.speaking.get(user)
        if speech is not None:
            speech.stop = offset
            speech.deadline = at + self.pause

    def _add(self, user: int, frame: Frame) -> list[Utterance]:
        ended: list[Utterance] = []
        speech = self.speaking.get(user)
        if speech is not None and (
            frame.offset - speech.end >= self.silence
            or frame.offset + frame.samples - speech.offset > self.longest
        ):
            ended.append(self.speaking.pop(user).utterance(user))
            speech = None
        if speech is None:
            speech = self.speaking[user] = _Speech(frame.offset, frame.offset)
        gap = max(frame.offset - speech.end, 0) * self.rate // SAMPLE_RATE
        speech.data += bytes(gap * self.width)
        speech.data += frame.data
        if self.level is not None:
            speech.deadline = frame.received_at + self.pause
        speech.end = frame.offset + frame.samples
        if frame.lost:
            speech.lost += frame.samples
        return ended


class _OggStream:
    def __init__(self, file: BinaryIO, serial: int) -> None:
        self.file = file
        self.serial = serial
        self.sequence = 0
        self.granule = 0
        self.samples = 0
        self.lacing = bytearray()
        self.body = bytearray()
        self.pages = bytearray()
        for flags, packet in ((BEGINNING_OF_STREAM, OPUS_HEAD), (0, OPUS_TAGS)):
            self.lacing += _lacing(packet)
            self.body += packet
            self.flush(flags)

    def add(self, packet: bytes, samples: int, count: int = 1) -> None:
        lacing = _lacing(packet)
        while count > 0:
            full = len(self.lacing) + len(lacing) > MAX_SEGMENTS
            if full or self.samples >= PAGE_SAMPLES:
                self.flush()
            fit = min(
                count,
                (MAX_SEGMENTS - len(self.lacing)) // len(lacing),
                math.ceil((PAGE_SAMPLES - self.samples) / samples),
            )
            self.lacing += lacing * fit
            self.body += packet * fit
            self.samples += samples * fit
            self.granule += samples * fit
            count -= fit

    def flush(self, flags: int = 0) -> None:
        header = struct.pack(
            "<4sBBqIIIB",
            b"OggS",
            0,
            flags,
            self.granule,
            self.serial,
            self.sequence,
            0,
            len(self.lacing),
        )
        page = bytearray(header) + self.lacing + self.body
        page[22:26] = _ogg_crc32(page)
        self.pages += page
        self.sequence += 1
        self.samples = 0
        self.lacing.clear()
        self.body.clear()

    def take(self) -> bytes:
        pages = bytes(self.pages)
        self.pages.clear()
        return pages


class _OggFile:
    def __init__(self, file: BinaryIO, serial: int, *, encode: bool) -> None:
        self.file = file
        self.stream = _OggStream(file, serial)
        self.encoder = OpusEncoder() if encode else None

    def add(self, frame: Frame, *, align: bool) -> bytes:
        stream = self.stream
        if self.encoder is not None:
            for packet in self.encoder.encode(frame.data):
                stream.add(packet, SILENCE_SAMPLES)
        elif not frame.lost and len(frame.data) <= MAX_PACKET:
            if align:
                gap = round((frame.offset - stream.granule) / SILENCE_SAMPLES)
                stream.add(SILENCE, SILENCE_SAMPLES, max(gap, 0))
            stream.add(frame.data, frame.samples)
        return stream.take()

    def write(self, data: bytes) -> None:
        self.file.write(data)
        self.file.flush()

    def close(self) -> None:
        with self.file:
            for packet in [] if self.encoder is None else self.encoder.flush():
                self.stream.add(packet, SILENCE_SAMPLES)
            self.stream.flush(END_OF_STREAM)
            self.file.write(self.stream.take())


class _WavFile:
    def __init__(self, file: BinaryIO, format: PCM) -> None:
        self.file = file
        self.width = format.channels * SAMPLE_BYTES[format.sample]
        self.rate = format.rate
        self.end = 0
        self.wav = wave.Wave_write(file)
        self.wav.setnchannels(format.channels)
        self.wav.setsampwidth(SAMPLE_BYTES[format.sample])
        self.wav.setframerate(format.rate)

    def add(self, frame: Frame, *, align: bool) -> bytes:
        gap = max(frame.offset - self.end, 0) if align else 0
        self.end = frame.offset + frame.samples
        return bytes(gap * self.rate // SAMPLE_RATE * self.width) + frame.data

    def write(self, data: bytes) -> None:
        self.wav.writeframesraw(data)
        self.file.flush()

    def close(self) -> None:
        with self.file:
            self.wav.close()


class Recorder:
    """Record each user, or with `mix=True` the whole call, to Ogg Opus or WAV.

    The path's extension picks the container; `format` is the WAV sample format.
    It ends on its own after `max_duration` seconds or once its files hold
    `max_size` bytes.
    """

    def __init__(
        self,
        vc: "VoiceConnection",
        path: str | os.PathLike[str],
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        align: bool = True,
        mix: bool = False,
        format: PCM = DEFAULT_PCM,
        buffer: float = 30.0,
        jitter: float = 0.3,
        max_duration: float | None = None,
        max_size: int | None = None,
    ) -> None:
        if max_duration is not None and not (
            math.isfinite(max_duration) and max_duration > 0
        ):
            raise ValueError(
                f"max_duration must be a positive number of seconds, got {max_duration}"
            )
        if max_size is not None and max_size <= 0:
            raise ValueError(f"max_size must be a positive byte count, got {max_size}")
        self._template = _template(path, mix=mix)
        self._wav = self._template.endswith(".wav")
        if self._wav and format.sample != "s16le":
            raise ValueError(f"a WAV recording takes s16le samples, got {format}")
        self._format = format if self._wav else DEFAULT_PCM
        self._guild_id = vc.guild_id
        self._align = align
        self._mix = mix
        self._max_duration = max_duration
        self._end = math.inf if max_duration is None else max_duration * SAMPLE_RATE
        self._room = math.inf if max_size is None else max_size
        self._halting: asyncio.Task[None] | None = None
        self._files: dict[int | None, Path] = {}
        self._writers: dict[int | None, _OggFile | _WavFile] = {}
        self._error: Exception | None = None
        self._stopping: asyncio.Future[None] = (
            asyncio.get_running_loop().create_future()
        )
        receiving: dict[str, Any] = {
            "users": users,
            "exclude": exclude,
            "include_self": include_self,
            "buffer": buffer,
            "jitter": jitter,
            "stall": "wait",
        }
        if self._wav or mix:
            receiving["mix"] = mix
        self._rx: Receiver[Frame] = vc.receive(
            self._format if self._wav or mix else Opus(), **receiving
        )
        self._task = asyncio.create_task(self._record())

    @property
    def files(self) -> Mapping[int | None, Path]:
        """The files written so far, by user id; None keys a mixed recording."""
        return MappingProxyType(self._files)

    def stats(self) -> "ReceiverStats":
        """The receiver's counters; dropped means the disk fell behind for too long."""
        return self._rx.stats()

    async def stop(self) -> dict[int | None, Path]:
        """Stop once heard audio is queued or the wait gives up; return the files."""
        await self._halt()
        return await self.wait()

    async def wait(self) -> dict[int | None, Path]:
        """Wait until the recording ends on its own, then return the files."""
        await asyncio.wait({self._task})
        if self._error is not None:
            raise self._error
        return dict(self._files)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def _record(self) -> None:
        loop = asyncio.get_running_loop()
        limit = (
            None
            if self._max_duration is None
            else loop.call_later(self._max_duration, self._expire)
        )
        try:
            await self._consume()
        except Exception as error:
            self._fail(error)
        finally:
            if limit is not None:
                limit.cancel()
            self._rx.close()
            await self._finish()

    def _expire(self) -> None:
        self._halting = asyncio.create_task(self._halt())

    async def _halt(self) -> None:
        await self._rx.mark()
        if not self._stopping.done():
            self._stopping.set_result(None)

    async def _consume(self) -> None:
        while self._room > 0:
            try:
                frame = self._rx.get_nowait()
            except ReceiverClosed:
                return
            except Empty:
                if self._stopping.done():
                    return
                arrived = await self._next()
                if arrived is None:
                    continue
                frame = arrived
            await self._write(frame)

    async def _next(self) -> Frame | None:
        reading = asyncio.ensure_future(anext(self._rx))
        either: set[asyncio.Future[Any]] = {reading, self._stopping}
        try:
            await asyncio.wait(either, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if reading.cancel():
                await asyncio.wait({reading})
        if reading.cancelled() or isinstance(reading.exception(), StopAsyncIteration):
            return None
        return reading.result()

    async def _write(self, frame: Frame) -> None:
        user = frame.user_id
        if (user is None and not self._mix) or frame.offset + frame.samples > self._end:
            return
        writer = self._writers.get(user)
        if writer is None:
            fields = {"guild_id": self._guild_id, "user_id": user}
            path = Path(self._template.format(**fields))
            file = await asyncio.to_thread(_create, path)
            writer = self._writers[user] = (
                _WavFile(file, self._format)
                if self._wav
                else _OggFile(file, (user or 0) & 0xFFFFFFFF, encode=self._mix)
            )
            self._files[user] = path
        data = writer.add(frame, align=self._align)
        if data:
            await asyncio.to_thread(writer.write, data)
            self._room -= len(data)

    async def _finish(self) -> None:
        for writer in self._writers.values():
            try:
                await asyncio.to_thread(writer.close)
            except Exception as error:
                self._fail(error)

    def _fail(self, error: Exception) -> None:
        if self._error is None:
            self._error = error
            log.error("recording to %s failed", self._template, exc_info=error)


def _template(path: str | os.PathLike[str], *, mix: bool) -> str:
    template = os.fspath(path)
    if not template.endswith((".ogg", ".opus", ".wav")):
        raise ValueError(f"path must end in .ogg, .opus or .wav, got {template!r}")
    fields = {
        name for _, name, _, _ in string.Formatter().parse(template) if name is not None
    }
    if mix and "user_id" in fields:
        raise ValueError(
            f"path of a mixed recording names one file, without {{user_id}}, "
            f"got {template!r}"
        )
    if not mix and "user_id" not in fields:
        raise ValueError(
            f"path must contain {{user_id}} to give each user a file, got {template!r}"
        )
    if not fields <= TEMPLATE_FIELDS:
        raise ValueError(
            f"path may use only {{guild_id}} and {{user_id}}, got {template!r}"
        )
    template.format(guild_id=0, user_id=0)
    return template


def _lacing(packet: bytes) -> bytes:
    return bytes([255] * (len(packet) // 255) + [len(packet) % 255])


def _ogg_crc32(page: bytearray) -> bytes:
    crc = zlib.crc32(page.translate(REVERSED_BITS), 0xFFFFFFFF) ^ 0xFFFFFFFF
    return crc.to_bytes(4, "big").translate(REVERSED_BITS)


def _create(path: Path) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("wb")
