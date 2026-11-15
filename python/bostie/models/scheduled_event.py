from datetime import datetime

from bostie.enums import (
    Month,
    RecurrenceFrequency,
    ScheduledEventEntityType,
    ScheduledEventPrivacyLevel,
    ScheduledEventStatus,
    Weekday,
)
from bostie.models.base import Builder, Model, Snowflake
from bostie.models.guild import Member
from bostie.models.user import User


class ScheduledEventEntityMetadata(Builder, kw_only=True):
    location: str | None = None


class RecurrenceRuleNWeekday(Builder, kw_only=True):
    """A weekday in the n-th week of a month."""

    n: int
    day: Weekday


class RecurrenceRule(Builder, kw_only=True):
    """How often an event recurs; only Discord sets `end`, `by_year_day` and `count`."""

    start: datetime
    end: datetime | None = None
    frequency: RecurrenceFrequency
    interval: int
    by_weekday: tuple[Weekday, ...] | None = None
    by_n_weekday: tuple[RecurrenceRuleNWeekday, ...] | None = None
    by_month: tuple[Month, ...] | None = None
    by_month_day: tuple[int, ...] | None = None
    by_year_day: tuple[int, ...] | None = None
    count: int | None = None


class ScheduledEvent(Model, Snowflake, kw_only=True):
    """An event planned in a guild."""

    id: int
    guild_id: int
    channel_id: int | None
    creator_id: int | None = None
    name: str
    description: str | None = None
    scheduled_start_time: datetime
    scheduled_end_time: datetime | None
    privacy_level: ScheduledEventPrivacyLevel
    status: ScheduledEventStatus
    entity_type: ScheduledEventEntityType
    entity_id: int | None
    entity_metadata: ScheduledEventEntityMetadata | None
    creator: User | None = None
    user_count: int | None = None
    image: str | None = None
    recurrence_rule: RecurrenceRule | None


class ScheduledEventUser(Model, kw_only=True):
    """A user subscribed to a scheduled event, with their member if asked for."""

    guild_scheduled_event_id: int
    user: User
    member: Member | None = None
