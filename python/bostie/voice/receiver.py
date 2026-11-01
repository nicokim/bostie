import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self, cast

from bostie._aio import Wake
from bostie.enums import ClosedReason, State
from bostie.errors import Empty, ReceiverClosed
from bostie.voice.events import SpeechStart, SpeechStop
from bostie.voice.formats import Overflow, ReceiveFormat, ReceiveStall
from bostie.voice.frames import Frame, Packet

if TYPE_CHECKING:
    from bostie._native import ClosedReasonName, ReceivedItem
    from bostie.voice.connection import VoiceConnection

READ_LIMIT = 256

type Item = Frame | Packet | SpeechStart | SpeechStop


class NativeReceiver(Protocol):
    @property
    def id(self) -> int: ...
    def read(self, limit: int) -> "list[ReceivedItem] | None": ...
    def queued(self) -> float: ...
    def clear(self) -> None: ...
    def mark(self) -> int: ...
    def stats(self) -> tuple[int, int, int]: ...
    def closed_reason(self) -> "ClosedReasonName | None": ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ReceiverStats:
    """What this receiver delivered, dropped and saw lost."""

    delivered: int
    dropped: int
    lost: int


class Receiver[T: Frame | Packet | SpeechStart | SpeechStop]:
    """A bounded async iterator over received audio; one per subscriber."""

    def __init__(
        self,
        connection: "VoiceConnection",
        native: NativeReceiver,
        format: ReceiveFormat,
        *,
        users: frozenset[int] | None,
        exclude: frozenset[int],
        include_self: bool,
        buffer: float,
        jitter: float,
        markers: bool,
        mix: bool,
        overflow: Overflow,
        stall: ReceiveStall,
    ) -> None:
        self.format = format
        self.users = users
        self.exclude = exclude
        self.include_self = include_self
        self.buffer = buffer
        self.jitter = jitter
        self.markers = markers
        self.mix = mix
        self.overflow = overflow
        self.stall = stall
        self._connection = connection
        self._native = native
        self._handle = native.id
        self._items: deque[tuple[float, Item]] = deque()
        self._woken = Wake()
        self._marks: dict[int, asyncio.Future[bool]] = {}
        self._stats = ReceiverStats(0, 0, 0)
        self._closed_reason: ClosedReason | None = None

    @property
    def queued(self) -> float:
        """Seconds the oldest queued item has waited: this is the consumer's lag."""
        if self.closed:
            return 0.0
        if not self._items:
            return self._native.queued()
        return time.monotonic() - self._items[0][0]

    @property
    def closed(self) -> bool:
        """Whether the receiver ended and nothing is left to read."""
        return self._closed_reason is not None

    @property
    def closed_reason(self) -> ClosedReason | None:
        """None while open, else why it ended."""
        return self._closed_reason

    def get_nowait(self) -> T:
        """Return the next item, or raise voice.Empty."""
        if not self._items and not self.closed:
            self._read()
        if self._items:
            return cast("T", self._items.popleft()[1])
        if self.closed:
            raise ReceiverClosed("the receiver ended and has nothing left to read")
        raise Empty("nothing is queued in the receiver")

    def clear(self) -> None:
        """Drop everything queued and continue live."""
        if not self.closed:
            self._items.clear()
            self._native.clear()

    def mark(self) -> Awaitable[bool]:
        """True once all audio heard so far is queued; False if the receiver ends."""
        future = asyncio.get_running_loop().create_future()
        if self.closed or self._connection.state is State.DISCONNECTED:
            future.set_result(False)
        else:
            self._marks[self._native.mark()] = future
        return future

    def close(self) -> None:
        """Stop receiving and discard what is queued."""
        if self.closed:
            return
        with contextlib.suppress(RuntimeError):
            self._stats = self._read_stats()
        self._native.close()
        self._end(ClosedReason.CLOSED)

    def stats(self) -> ReceiverStats:
        """The latest counters, or the last ones read before the receiver ended."""
        if not self.closed:
            self._stats = self._read_stats()
        return self._stats

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> T:
        while not self._items:
            if self.closed:
                raise StopAsyncIteration
            read = self._read()
            if read == READ_LIMIT:
                await asyncio.sleep(0)
            elif not read and not self.closed:
                await self._woken.wait()
        return cast("T", self._items.popleft()[1])

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()

    def _read(self) -> int:
        now = time.monotonic()
        batch = self._native.read(READ_LIMIT)
        if batch is None or (
            not batch and self._connection.state is State.DISCONNECTED
        ):
            self._drained()
            return 0
        guild_id = self._connection.guild_id
        self._items.extend(_convert(item, now, guild_id) for item in batch)
        return len(batch)

    def _drained(self) -> None:
        reason = self._native.closed_reason()
        with contextlib.suppress(RuntimeError):
            self._stats = self._read_stats()
        self._end(ClosedReason.DISCONNECTED if reason is None else ClosedReason(reason))

    def _end(self, reason: ClosedReason) -> None:
        self._closed_reason = reason
        self._items.clear()
        self._release()

    def _release(self) -> None:
        self._wake()
        for future in self._marks.values():
            if not future.done():
                future.set_result(False)
        self._marks.clear()
        self._connection._forget_receiver(self)

    def _wake(self) -> None:
        self._woken.set()

    def _marked(self, id: int, *, reached: bool) -> None:
        future = self._marks.pop(id, None)
        if future is not None and not future.done():
            future.set_result(reached)

    def _read_stats(self) -> ReceiverStats:
        return ReceiverStats(*self._native.stats())


def _convert(item: "ReceivedItem", now: float, guild_id: int) -> tuple[float, Item]:
    match item:
        case ("frame", user, ssrc, timestamp, offset, samples, age, data, lost, queued):
            return now - queued, Frame(
                user, ssrc, timestamp, offset, samples, now - age, data, lost
            )
        case ("packet", user, ssrc, sequence, timestamp, age, opus, extensions, queued):
            return now - queued, Packet(
                user, ssrc, sequence, timestamp, now - age, opus, tuple(extensions)
            )
        case ("speech", user, ssrc, True, offset, age, _, queued):
            return now - queued, SpeechStart(
                guild_id=guild_id, user_id=user, ssrc=ssrc, at=now - age, offset=offset
            )
        case ("speech", user, ssrc, False, offset, age, duration, queued):
            return now - queued, SpeechStop(
                guild_id=guild_id,
                user_id=user,
                ssrc=ssrc,
                at=now - age,
                duration=duration,
                offset=offset,
            )
