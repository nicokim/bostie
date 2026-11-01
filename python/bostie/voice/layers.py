import asyncio
import logging
import math
import os
import string
import struct
import time
import zlib
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, BinaryIO, Self, Unpack

from bostie._aio import spawn
from bostie.enums import State
from bostie.errors import (
    Empty,
    ReceiverClosed,
    SinkClosed,
    SourceError,
    VoiceNotConnected,
)
from bostie.models.base import check_keys
from bostie.voice.encoder import DEFAULT_ENCODER, OpusEncoder
from bostie.voice.events import SpeechStart, SpeechStop
from bostie.voice.formats import (
    PCM,
    SAMPLE_RATE,
    SINK_OPTIONS,
    EncoderSettings,
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
        self, vc: "VoiceConnection", *, volume: float, options: SinkOptions
    ) -> None:
        self.vc = vc
        self.closed = False
        self._volume = volume
        self._options = options
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
            upcoming = self._entries[following]
            upcoming.opening = self._opening(upcoming)

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
        spawn(self._tasks, self._finish(entry, entry.end))

    async def _sink_for(self, format: Format) -> "Sink":
        if self._sink is not None and format == self._format:
            return self._sink
        if self._sink is not None:
            previous = self._sink
            await self._drain(previous)
            self._sink = None
            previous.close()
        self._sink = self.vc.sink(format, volume=self._volume, **self._options)
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
        **options: Unpack[SinkOptions],
    ) -> None:
        check_keys(options, SINK_OPTIONS, "options")
        self.vc = vc
        self._player = _Player(vc, volume=volume, options=options)

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
    meta: Any = None,
    **options: Unpack[SinkOptions],
) -> Track:
    check_keys(options, SINK_OPTIONS, "options")
    try:
        await source.open(vc.manager)
        sink = vc.sink(source.format, volume=volume, **options)
    except BaseException:
        await source.aclose()
        raise
    player = _Player(vc, volume=volume, options=options)
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
    rx: "Receiver[Frame | SpeechStart | SpeechStop]",
    *,
    min_silence: float = 0.8,
    max_length: float = 30.0,
) -> AsyncIterator[Utterance]:
    """Group a receiver's frames into utterances per user."""
    format = rx.format
    if not isinstance(format, PCM) or not rx.markers or rx.mix:
        raise ValueError(
            "utterances needs a PCM receiver opened with markers=True and no mix"
        )
    return _utterances(rx, format, min_silence, max_length)


async def _utterances(
    rx: "Receiver[Frame | SpeechStart | SpeechStop]",
    format: PCM,
    min_silence: float,
    max_length: float,
) -> AsyncIterator[Utterance]:
    grouping = _Grouping(
        silence=round(min_silence * SAMPLE_RATE),
        longest=round(max_length * SAMPLE_RATE),
        pause=rx.jitter + min_silence,
        width=format.channels * (2 if format.sample == "s16le" else 4),
        rate=format.rate,
    )
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
    rx: "Receiver[Frame | SpeechStart | SpeechStop]", deadline: float
) -> Frame | SpeechStart | SpeechStop | None:
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
    speaking: dict[int, _Speech] = field(default_factory=dict)

    def deadline(self) -> float:
        return min(
            (speech.deadline for speech in self.speaking.values()), default=math.inf
        )

    def take(
        self, item: Frame | SpeechStart | SpeechStop | None, deadline: float
    ) -> list[Utterance]:
        match item:
            case None:
                return self._expire(deadline)
            case SpeechStart(user_id=user, offset=int(offset)):
                return self._start(user, offset)
            case SpeechStop(user_id=user, offset=int(offset), at=at):
                self._stop(user, offset, at)
            case Frame(user_id=int(user)):
                return self._add(user, item)
            case _:
                pass
        return []

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


class Recorder:
    """Record each user to an Ogg Opus file as the call goes."""

    def __init__(
        self,
        vc: "VoiceConnection",
        path: str | os.PathLike[str],
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        align: bool = True,
        buffer: float = 30.0,
        jitter: float = 0.3,
    ) -> None:
        self._template = _template(path)
        self._guild_id = vc.guild_id
        self._align = align
        self._files: dict[int, Path] = {}
        self._streams: dict[int, _OggStream] = {}
        self._error: Exception | None = None
        self._stopping: asyncio.Future[None] = (
            asyncio.get_running_loop().create_future()
        )
        self._rx = vc.receive(
            Opus(),
            users=users,
            exclude=exclude,
            include_self=include_self,
            buffer=buffer,
            jitter=jitter,
            stall="wait",
        )
        self._task = asyncio.create_task(self._record())

    @property
    def files(self) -> Mapping[int, Path]:
        """The files written so far, by user id."""
        return MappingProxyType(self._files)

    def stats(self) -> "ReceiverStats":
        """The receiver's counters; dropped means the disk fell behind for too long."""
        return self._rx.stats()

    async def stop(self) -> dict[int, Path]:
        """Stop recording, finish every file and return them by user id."""
        await self._rx.mark()
        if not self._stopping.done():
            self._stopping.set_result(None)
        await asyncio.wait({self._task})
        if self._error is not None:
            raise self._error
        return dict(self._files)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def _record(self) -> None:
        try:
            await self._consume()
        except Exception as error:
            self._fail(error)
        finally:
            self._rx.close()
            await self._finish()

    async def _consume(self) -> None:
        while True:
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
        if user is None or frame.lost or len(frame.data) > MAX_PACKET:
            return
        stream = self._streams.get(user)
        if stream is None:
            path = Path(self._template.format(guild_id=self._guild_id, user_id=user))
            file = await asyncio.to_thread(_create, path)
            stream = self._streams[user] = _OggStream(file, user & 0xFFFFFFFF)
            self._files[user] = path
        if self._align:
            gap = round((frame.offset - stream.granule) / SILENCE_SAMPLES)
            stream.add(SILENCE, SILENCE_SAMPLES, max(gap, 0))
        stream.add(frame.data, frame.samples)
        if stream.pages:
            await asyncio.to_thread(_append, stream.file, stream.take())

    async def _finish(self) -> None:
        for stream in self._streams.values():
            stream.flush(END_OF_STREAM)
            try:
                await asyncio.to_thread(_close, stream.file, stream.take())
            except Exception as error:
                self._fail(error)

    def _fail(self, error: Exception) -> None:
        if self._error is None:
            self._error = error
            log.error("recording to %s failed", self._template, exc_info=error)


def _template(path: str | os.PathLike[str]) -> str:
    template = os.fspath(path)
    if not template.endswith((".ogg", ".opus")):
        raise ValueError(f"path must end in .ogg or .opus, got {template!r}")
    fields = {
        name for _, name, _, _ in string.Formatter().parse(template) if name is not None
    }
    if "user_id" not in fields:
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


def _append(file: BinaryIO, pages: bytes) -> None:
    file.write(pages)
    file.flush()


def _close(file: BinaryIO, pages: bytes) -> None:
    with file:
        file.write(pages)
