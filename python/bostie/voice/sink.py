import asyncio
import contextlib
from collections.abc import Buffer, Callable, Generator, Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, Final, Self

from bostie.errors import BostieError, BufferFull, SinkClosed
from bostie.voice.formats import (
    NO_FILTERS,
    SAMPLE_BYTES,
    SAMPLE_RATE,
    Filters,
    Format,
    Opus,
    SinkStall,
)

if TYPE_CHECKING:
    from bostie import _native
    from bostie.voice._link import Handle

MIN_PIECE = 0.02


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
        native: "_native.Sink",
        handle: "Handle",
        format: Format,
        *,
        buffer: float,
        stall: SinkStall,
        volume: float,
        crossfade: float = 0.0,
        filters: Filters = NO_FILTERS,
        on_audio: Callable[[], None],
        on_close: Callable[["Sink"], None],
    ) -> None:
        self.format: Final = format
        self.buffer: Final = buffer
        self.stall: Final = stall
        self.crossfade: Final = crossfade
        self._native = native
        self._handle = handle
        self._room = handle.woken
        self._on_audio = on_audio
        self._on_close = on_close
        self._volume = volume
        self._filters = NO_FILTERS
        self._piece = _piece_bytes(format, buffer)
        self._stats = SinkStats(0.0, 0.0, 0, 0, 0.0, 0)
        self._playing = False
        if filters != NO_FILTERS:
            self.filters = filters

    @property
    def queued(self) -> float:
        """Seconds of audio waiting to be sent."""
        return 0.0 if self._handle.closed else self._native.queued()

    @property
    def passthrough(self) -> bool:
        """Whether the last packet went out as written, without decoding."""
        return False if self._handle.closed else self._native.passthrough()

    @property
    def closed(self) -> bool:
        return self._handle.closed

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, volume: float) -> None:
        if not self._handle.closed:
            self._native.set_volume(volume)
        self._volume = volume

    @property
    def filters(self) -> Filters:
        """The effects Rust applies as this sink plays; changes take effect at once."""
        return self._filters

    @filters.setter
    def filters(self, filters: Filters) -> None:
        if not self._handle.closed:
            bands = list(filters.equalizer.items())
            self._native.set_filters(bands, filters.bass, filters.speed, filters.pitch)
        self._filters = filters

    async def write(self, data: Buffer) -> None:
        """Queue audio, waiting while the sink holds `buffer` seconds or more."""
        self._check_open()
        for piece in self._pieces(memoryview(data).cast("B")):
            while not self._native.write(piece):
                await self._room.wait()
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
        if self._handle.closed:
            return False
        return await self._marker(*self._native.flush())

    def clear(
        self, *, until: Marker | None = None, since: Marker | None = None
    ) -> None:
        """Drop the queued audio, only the part before `until`, or after `since`."""
        if until is not None and since is not None:
            raise ValueError("clear takes until or since, not both")
        if not self._handle.closed:
            self._native.clear(
                None if until is None else until.id,
                None if since is None else since.id,
            )

    def close(self) -> None:
        """Discard the queued audio and close; `async with` drains first."""
        if self._handle.closed:
            return
        with contextlib.suppress(BostieError):
            self._stats = self._read_stats()
        self._native.close()
        self._handle.close()
        self._on_close(self)

    def stats(self) -> SinkStats:
        """The latest counters, or the last ones read before the sink closed."""
        if not self._handle.closed:
            self._stats = self._read_stats()
        return self._stats

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                await self.drain()
        finally:
            self.close()

    def _check_open(self) -> None:
        if self._handle.closed:
            raise SinkClosed("the sink is closed")

    def _pieces(self, view: memoryview) -> Iterable[memoryview]:
        if isinstance(self.format, Opus):
            return (view,)
        step = self._piece
        return (view[start : start + step] for start in range(0, len(view), step))

    def _started(self) -> None:
        if not self._playing:
            self._playing = True
            self._on_audio()

    def _marker(self, id: int, position: int) -> Marker:
        return Marker(id, position / SAMPLE_RATE, self._handle.marker(id))

    def _read_stats(self) -> SinkStats:
        sent, consumed, underruns, held_ticks, dropped, passthrough = (
            self._native.stats()
        )
        return SinkStats(
            sent / SAMPLE_RATE,
            consumed / SAMPLE_RATE,
            underruns,
            held_ticks,
            dropped / SAMPLE_RATE,
            passthrough,
        )


def _piece_bytes(format: Format, buffer: float) -> int:
    if isinstance(format, Opus):
        return 0
    frames = int(format.rate * max(MIN_PIECE, buffer / 4))
    return frames * format.channels * SAMPLE_BYTES[format.sample]
