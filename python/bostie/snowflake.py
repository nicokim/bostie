from datetime import UTC, datetime, timedelta

DISCORD_EPOCH = datetime(2015, 1, 1, tzinfo=UTC)
ONE_MS = timedelta(milliseconds=1)
LOW_BITS = (1 << 22) - 1


def snowflake_time(snowflake: int) -> datetime:
    """When the object with this id was created, in UTC."""
    return DISCORD_EPOCH + (snowflake >> 22) * ONE_MS


def time_snowflake(when: datetime, *, high: bool = False) -> int:
    """The lowest id made at `when`, or with `high` the highest; naive is local time."""
    milliseconds = (when.astimezone(UTC) - DISCORD_EPOCH) // ONE_MS
    return milliseconds << 22 | (LOW_BITS if high else 0)
