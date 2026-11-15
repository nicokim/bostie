import asyncio
from datetime import UTC, datetime, time, timedelta, timezone, tzinfo
from typing import Self, override

import pytest

from bostie import tasks
from bostie.tasks import Task, next_time

UTC_PLUS_2 = timezone(timedelta(hours=2))
AFTER = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("times", "due"),
    [
        ([time(10)], datetime(2026, 11, 1, 10, tzinfo=UTC)),
        ([time(9, 30)], datetime(2026, 11, 2, 9, 30, tzinfo=UTC)),
        ([time(8), time(23), time(12)], datetime(2026, 11, 1, 12, tzinfo=UTC)),
        (
            [time(11, 45, tzinfo=UTC_PLUS_2)],
            datetime(2026, 11, 1, 9, 45, tzinfo=UTC),
        ),
        ([time(1, tzinfo=UTC_PLUS_2)], datetime(2026, 11, 1, 23, tzinfo=UTC)),
    ],
)
def test_next_time_is_the_first_named_time_after_the_moment(
    times: list[time], due: datetime
) -> None:
    assert next_time(times, AFTER) == due


@pytest.mark.parametrize(
    ("seconds", "times", "error"),
    [
        (None, (), TypeError),
        (60.0, (time(9),), TypeError),
        (0.0, (), ValueError),
    ],
)
def test_a_task_takes_positive_seconds_or_times_but_not_both(
    seconds: float | None, times: tuple[time, ...], error: type[Exception]
) -> None:
    async def callback() -> None:
        pass

    with pytest.raises(error):
        Task(callback, seconds, times)


def test_a_daily_task_runs_once_at_each_time_even_when_a_sleep_ends_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [datetime(2026, 11, 1, 8, tzinfo=UTC)]
    early = timedelta(milliseconds=1)
    yield_once = asyncio.sleep
    calls: list[datetime] = []
    errors: list[Exception] = []

    class Clock(datetime):
        @classmethod
        @override
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(clock[0].timestamp(), tz)

    async def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds) - early
        await yield_once(0)

    async def main() -> None:
        third = asyncio.Event()

        async def callback() -> None:
            calls.append(clock[0])
            if len(calls) == 3:
                third.set()

        task = Task(callback, None, (time(21), time(9)))
        running = asyncio.ensure_future(
            task.run(lambda callback, error: errors.append(error))
        )
        async with asyncio.timeout(10):
            await third.wait()
        running.cancel()

    monkeypatch.setattr(tasks, "datetime", Clock)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    asyncio.run(main())

    assert calls == [
        datetime(2026, 11, 1, 9, tzinfo=UTC) - early,
        datetime(2026, 11, 1, 21, tzinfo=UTC) - early,
        datetime(2026, 11, 2, 9, tzinfo=UTC) - early,
    ]
    assert errors == []
