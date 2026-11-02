from datetime import UTC, datetime, time, timedelta, timezone

import pytest

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
