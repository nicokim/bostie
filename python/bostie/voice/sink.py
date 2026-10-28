import asyncio
import contextlib
from collections.abc import Buffer, Generator, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, Self

from bostie.errors import BufferFull, SinkClosed
from bostie.voice.formats import SAMPLE_RATE, Format, Opus

if TYPE_CHECKING:
    from bostie.voice.connection import VoiceConnection

MIN_PIECE = 0.02
SAMPLE_BYTES = {"s16le": 2, "f32le": 4}


class NativeSink(Protocol):
    @property
    def id(self) -> int: ...
    def write(self, data: Buffer) -> bool: ...
    def mark(self) -> tuple[int, int]: ...
    def flush(self) -> tuple[int, int]: ...
    def clear(self, until: int | None = None) -> None: ...
    def set_volume(self, volume: float) -> None: ...
    def queued(self) -> float: ...
    def passthrough(self) -> bool: ...
    def stats(self) -> dict[str, int]: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SinkStats:
    """A sink's counters; `position` is its playhead in seconds, as markers count."""

    sent: float
    position: float
    underruns: int
    held_ticks: int
    dropped: float
    passthrough_packets: int


class Marker:
    """A point in a sink's audio; awaiting it says whether all audio before it played.

    Await it as often as you like; cancelling one wait leaves the others intact.
    """

    def __init__(self, id: int, position: float, future: asyncio.Future[bool]) -> None:
        self.id = id
        self.position = position
        self._future = future

    def done(self) -> bool:
        return self._future.done()

    def __await__(self) -> Generator[Any, None, bool]:
        return asyncio.shield(self._future).__await__()


class Sink:
    """A bounded writer whose audio is mixed into a voice connection."""

    def __init__(
        self,
        connection: "VoiceConnection",
        native: NativeSink,
        format: Format,
        *,
        buffer: float,
        stall: Literal["hold", "drop"],
        volume: float,
    ) -> None:
        self.format = format
        self.buffer = buffer
        self.stall = stall
        self._connection = connection
        self._native = native
        self._handle = native.id
        self._volume = volume
        self._piece = _piece_bytes(format, buffer)
        self._room: asyncio.Future[None] | None = None
        self._markers: dict[int, asyncio.Future[bool]] = {}
        self._stats = SinkStats(0.0, 0.0, 0, 0, 0.0, 0)
        self._closed = False
        self._playing = False

    @property
    def queued(self) -> float:
        """Seconds of audio waiting to be sent."""
        return 0.0 if self._closed else self._native.queued()

    @property
    def passthrough(self) -> bool:
        """Whether the last packet went out as written, without decoding."""
        return False if self._closed else self._native.passthrough()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, volume: float) -> None:
        if not self._closed:
            self._native.set_volume(volume)
        self._volume = volume

    async def write(self, data: Buffer) -> None:
        """Queue audio, waiting while the sink holds `buffer` seconds or more."""
        self._check_open()
        for piece in self._pieces(memoryview(data).cast("B")):
            while not self._native.write(piece):
                await self._wait_for_room()
                self._check_open()
            self._started()

    def write_nowait(self, data: Buffer) -> None:
        """Queue the whole chunk in one native write, or raise BufferFull.

        A large chunk overshoots `buffer` and converts in one call; `write()` slices it.
        """
        self._check_open()
        if not self._native.write(memoryview(data).cast("B")):
            raise BufferFull(f"the sink already holds {self.buffer} s of audio")
        self._started()

    def mark(self) -> Marker:
        """A marker after the audio written so far."""
        self._check_open()
        return self._marker(*self._native.mark())

    async def drain(self) -> bool:
        """Wait until everything written so far has left; False if any was dropped."""
        if self._closed:
            return False
        return await self._marker(*self._native.flush())

    def clear(self, *, until: Marker | None = None) -> None:
        """Drop the queued audio, or only the part before `until`."""
        if not self._closed:
            self._native.clear(None if until is None else until.id)

    def close(self) -> None:
        """Discard the queued audio and close; `async with` drains first."""
        if self._closed:
            return
        with contextlib.suppress(RuntimeError):
            self._stats = self._read_stats()
        self._native.close()
        self._release()

    def stats(self) -> SinkStats:
        """The latest counters, or the last ones read before the sink closed."""
        if not self._closed:
            self._stats = self._read_stats()
        return self._stats

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        drained = False
        try:
            if exc[0] is None:
                await self.drain()
                drained = True
        finally:
            try:
                if not drained:
                    self.clear()
            finally:
                self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise SinkClosed("the sink is closed")

    def _pieces(self, view: memoryview) -> Iterable[memoryview]:
        if isinstance(self.format, Opus):
            return (view,)
        step = self._piece
        return (view[start : start + step] for start in range(0, len(view), step))

    async def _wait_for_room(self) -> None:
        if self._room is None:
            self._room = asyncio.get_running_loop().create_future()
        await asyncio.shield(self._room)

    def _started(self) -> None:
        if not self._playing:
            self._playing = True
            self._connection._audio_started()

    def _marker(self, id: int, position: int) -> Marker:
        future = self._markers[id] = asyncio.get_running_loop().create_future()
        return Marker(id, position / SAMPLE_RATE, future)

    def _wake(self) -> None:
        room, self._room = self._room, None
        if room is not None and not room.done():
            room.set_result(None)

    def _marked(self, id: int, played: bool) -> None:
        future = self._markers.pop(id, None)
        if future is not None and not future.done():
            future.set_result(played)

    def _release(self) -> None:
        self._closed = True
        self._wake()
        for future in self._markers.values():
            if not future.done():
                future.set_result(False)
        self._markers.clear()
        self._connection._forget(self)

    def _read_stats(self) -> SinkStats:
        raw = self._native.stats()
        return SinkStats(
            sent=raw.get("sent_samples", 0) / SAMPLE_RATE,
            position=raw.get("consumed_samples", 0) / SAMPLE_RATE,
            underruns=raw.get("underruns", 0),
            held_ticks=raw.get("held_ticks", 0),
            dropped=raw.get("dropped_samples", 0) / SAMPLE_RATE,
            passthrough_packets=raw.get("passthrough_packets", 0),
        )


def _piece_bytes(format: Format, buffer: float) -> int:
    if isinstance(format, Opus):
        return 0
    frames = int(format.rate * max(MIN_PIECE, buffer / 4))
    return frames * format.channels * SAMPLE_BYTES[format.sample]
