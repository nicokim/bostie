import asyncio
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

type TaskCallback = Callable[[], Coroutine[Any, Any, None]]
type TaskFailed = Callable[[TaskCallback, Exception], None]


@dataclass(frozen=True, slots=True)
class Task:
    """A callback the bot runs while ready: every `seconds`, or daily at `times`."""

    callback: TaskCallback
    seconds: float | None
    times: tuple[time, ...]

    def __post_init__(self) -> None:
        if (self.seconds is None) == (not self.times):
            raise TypeError("a task runs every `seconds` or at each `time`: pass one")
        if self.seconds is not None and self.seconds <= 0:
            raise ValueError(f"seconds must be positive, got {self.seconds}")

    async def run(self, failed: TaskFailed) -> None:
        if self.seconds is None:
            await self._daily(failed)
        else:
            await self._every(self.seconds, failed)

    async def _every(self, seconds: float, failed: TaskFailed) -> None:
        loop = asyncio.get_running_loop()
        start = loop.time()
        while True:
            await self._call(failed)
            await asyncio.sleep(seconds - (loop.time() - start) % seconds)

    async def _daily(self, failed: TaskFailed) -> None:
        due = datetime.now(UTC)
        while True:
            due = next_time(self.times, max(due, datetime.now(UTC)))
            await asyncio.sleep((due - datetime.now(UTC)).total_seconds())
            await self._call(failed)

    async def _call(self, failed: TaskFailed) -> None:
        try:
            await self.callback()
        except Exception as error:
            failed(self.callback, error)


def next_time(times: Iterable[time], after: datetime) -> datetime:
    """The first moment after `after` that one of `times` names; naive times are UTC."""
    return min(_next(at, after) for at in times)


def _next(at: time, after: datetime) -> datetime:
    zone = at.tzinfo or UTC
    day = after.astimezone(zone).date()
    wall = at.replace(tzinfo=None)
    due = datetime.combine(day, wall, zone)
    if due <= after:
        due = datetime.combine(day + timedelta(days=1), wall, zone)
    return due
