import asyncio
from datetime import UTC, datetime, time, timedelta, timezone, tzinfo
from typing import Self, override

import pytest
from clock import SteppedLoop, run_stepped

from bostie import Bot, Router, tasks
from bostie.tasks import _next_time
from bostie.testing import FakeDiscord, running, settle

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
    assert _next_time(times, AFTER) == due


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
        Router().every(seconds=seconds, time=times)(callback)


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

        task = Router().every(time=(time(21), time(9)))(callback)
        running = asyncio.ensure_future(
            task._run(lambda callback, error: errors.append(error))
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


async def taken(queue: asyncio.Queue[str], count: int) -> list[str]:
    return [await queue.get() for _ in range(count)]


def test_a_task_stops_starts_and_restarts_around_its_hooks_and_count() -> None:
    async def scenario(fake: FakeDiscord, loop: SteppedLoop) -> None:
        bot = Bot(api_url=fake.api_url)
        seen: asyncio.Queue[str] = asyncio.Queue()

        @bot.every(seconds=60)
        async def tick() -> None:
            seen.put_nowait(f"run {tick.count}")

        @tick.before
        async def opened() -> None:
            seen.put_nowait("before")

        @tick.after
        async def closed() -> None:
            seen.put_nowait("after")

        tick.stop()
        steps: list[list[str]] = []
        async with running(bot, fake):
            await settle()
            steps.append(await taken(seen, seen.qsize()))
            tick.start()
            steps.append(await taken(seen, 2))
            loop.advance(60)
            steps.append(await taken(seen, 1))
            tick.restart()
            steps.append(await taken(seen, 3))
            tick.stop()
            steps.append(await taken(seen, 1))
            loop.advance(120)
            await settle()
            steps.append(await taken(seen, seen.qsize()))
            tick.start()
            steps.append(await taken(seen, 2))
        steps.append(await taken(seen, 1))

        assert steps == [
            [],
            ["before", "run 0"],
            ["run 1"],
            ["after", "before", "run 0"],
            ["after"],
            [],
            ["before", "run 0"],
            ["after"],
        ]

    run_stepped(scenario)


def test_a_new_interval_waits_afresh_from_the_change() -> None:
    async def scenario(fake: FakeDiscord, loop: SteppedLoop) -> None:
        bot = Bot(api_url=fake.api_url)
        runs: asyncio.Queue[float] = asyncio.Queue()

        @bot.every(seconds=60)
        async def tick() -> None:
            runs.put_nowait(loop.time())

        async with running(bot, fake):
            first = await runs.get()
            loop.advance(30)
            tick.change_interval(seconds=10)
            await settle()
            loop.advance(9)
            await settle()
            early = not runs.empty()
            loop.advance(1)
            second = await runs.get()

        assert not early
        assert (tick.seconds, tick.times) == (10, ())
        assert second - first == pytest.approx(40, abs=0.05)

    run_stepped(scenario)
