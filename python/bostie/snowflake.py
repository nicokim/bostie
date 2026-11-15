from datetime import UTC, datetime, timedelta

_DISCORD_EPOCH = datetime(2015, 1, 1, tzinfo=UTC)
_ONE_MS = timedelta(milliseconds=1)
_LOW_BITS = (1 << 22) - 1


def snowflake_time(snowflake: int) -> datetime:
    """When the object with this id was created, in UTC."""
    return _DISCORD_EPOCH + (snowflake >> 22) * _ONE_MS


def time_snowflake(when: datetime, *, high: bool = False) -> int:
    """The lowest id made at `when`, or with `high` the highest; naive is local time."""
    milliseconds = (when.astimezone(UTC) - _DISCORD_EPOCH) // _ONE_MS
    return milliseconds << 22 | (_LOW_BITS if high else 0)


def shard_of(guild_id: int, shard_count: int) -> int:
    """The shard, out of `shard_count`, that Discord sends a guild's events to."""
    return (guild_id >> 22) % shard_count
