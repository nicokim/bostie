import asyncio
import datetime as dt
from collections.abc import Callable, Coroutine, Iterable
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from bostie.errors import StateError

if TYPE_CHECKING:
    from bostie.bot import Bot
    from bostie.router import Router

type TaskCallback = Callable[[], Coroutine[Any, Any, None]]
type TaskHook = Callable[[], Coroutine[Any, Any, None]]
type TaskFailed = Callable[["Task", Exception], None]


class Task:
    """What `@router.every` returns: a callback the bot runs while ready, on a schedule.

    It runs every `seconds`, or daily at each of `times`.
    """

    def __init__(
        self,
        callback: TaskCallback,
        *,
        seconds: float | None,
        times: dt.time | Iterable[dt.time],
        router: "Router",
    ) -> None:
        self.callback = callback
        self.router = router
        self.seconds, self.times = _schedule(seconds, times)
        self._count = 0
        self._stopped = False
        self._rescheduled = False
        self._before: TaskHook | None = None
        self._after: TaskHook | None = None
        self._sleep: asyncio.Future[None] | None = None

    async def __call__(self) -> None:
        await self.callback()

    @property
    def count(self) -> int:
        """How many runs ended since the task last started."""
        return self._count

    def start(self) -> None:
        """Run the task while the bot is ready, from now if it is; undoes `stop()`."""
        self._stopped = False
        self._sync()

    def stop(self) -> None:
        """Cancel the task, run or wait, and keep it from starting until `start()`."""
        self._stopped = True
        self._sync()

    def restart(self) -> None:
        """Cancel the task and start it anew, its count from 0."""
        self.stop()
        self.start()

    def change_interval(
        self, *, seconds: float | None = None, time: dt.time | Iterable[dt.time] = ()
    ) -> None:
        """Run every `seconds` or daily at `time` from now on, waiting afresh."""
        self.seconds, self.times = _schedule(seconds, time)
        self._rescheduled = True
        if self._sleep is not None:
            self._sleep.cancel()

    def before[H: TaskHook](self, hook: H) -> H:
        """Register `async def hook()`, awaited each time the task starts."""
        self._before = hook
        return hook

    def after[H: TaskHook](self, hook: H) -> H:
        """Register `async def hook()`, awaited whenever the task stops."""
        self._after = hook
        return hook

    async def run(self, failed: TaskFailed) -> None:
        self._count = 0
        try:
            if await self._hook(self._before, failed):
                await self._loop(failed)
        finally:
            await self._hook(self._after, failed)

    async def _loop(self, failed: TaskFailed) -> None:
        loop = asyncio.get_running_loop()
        anchor = loop.time()
        due = datetime.now(UTC)
        run = self.seconds is not None
        while True:
            if run:
                await self._call(failed)
            if self._rescheduled:
                self._rescheduled = False
                anchor, due = loop.time(), datetime.now(UTC)
            if self.seconds is not None:
                delay = self.seconds - (loop.time() - anchor) % self.seconds
            else:
                due = next_time(self.times, max(due, datetime.now(UTC)))
                delay = (due - datetime.now(UTC)).total_seconds()
            run = await self._wait(delay)

    async def _wait(self, delay: float) -> bool:
        sleep = self._sleep = asyncio.Task(
            asyncio.sleep(delay), loop=asyncio.get_running_loop(), eager_start=True
        )
        try:
            await asyncio.wait([sleep])
        finally:
            self._sleep = None
            sleep.cancel()
        return not sleep.cancelled()

    async def _call(self, failed: TaskFailed) -> None:
        try:
            await self.callback()
        except Exception as error:
            failed(self, error)
        self._count += 1

    async def _hook(self, hook: TaskHook | None, failed: TaskFailed) -> bool:
        if hook is None:
            return True
        try:
            await hook()
        except Exception as error:
            failed(self, error)
            return False
        return True

    def _sync(self) -> None:
        try:
            bot: Bot = self.router.bot
        except StateError:
            return
        bot._run_tasks()


def next_time(times: Iterable[time], after: datetime) -> datetime:
    """The first moment after `after` that one of `times` names; naive times are UTC."""
    return min(_next(at, after) for at in times)


def _schedule(
    seconds: float | None, times: time | Iterable[time]
) -> tuple[float | None, tuple[time, ...]]:
    chosen = (times,) if isinstance(times, dt.time) else tuple(times)
    if (seconds is None) == (not chosen):
        raise TypeError("a task runs every `seconds` or at each `time`: pass one")
    if seconds is not None and seconds <= 0:
        raise ValueError(f"seconds must be positive, got {seconds}")
    return seconds, chosen


def _next(at: time, after: datetime) -> datetime:
    zone = at.tzinfo or UTC
    day = after.astimezone(zone).date()
    wall = at.replace(tzinfo=None)
    due = datetime.combine(day, wall, zone)
    if due <= after:
        due = datetime.combine(day + timedelta(days=1), wall, zone)
    return due
