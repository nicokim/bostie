from datetime import datetime

from bostie.enums import (
    Month,
    RecurrenceFrequency,
    ScheduledEventEntityType,
    ScheduledEventPrivacyLevel,
    ScheduledEventStatus,
    Weekday,
)
from bostie.models.base import Model
from bostie.models.user import User


class ScheduledEventEntityMetadata(Model, kw_only=True):
    location: str | None = None


class RecurrenceRuleNWeekday(Model, kw_only=True):
    """A weekday in the n-th week of a month."""

    n: int
    day: Weekday


class RecurrenceRule(Model, kw_only=True):
    """How often a scheduled event recurs."""

    start: datetime
    end: datetime | None
    frequency: RecurrenceFrequency
    interval: int
    by_weekday: tuple[Weekday, ...] | None
    by_n_weekday: tuple[RecurrenceRuleNWeekday, ...] | None
    by_month: tuple[Month, ...] | None
    by_month_day: tuple[int, ...] | None
    by_year_day: tuple[int, ...] | None
    count: int | None


class ScheduledEvent(Model, kw_only=True):
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
