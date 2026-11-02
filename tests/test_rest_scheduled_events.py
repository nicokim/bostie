from datetime import UTC, datetime

from bostie import (
    Bot,
    RecurrenceFrequency,
    RecurrenceRule,
    ScheduledEventEntityMetadata,
    ScheduledEventEntityType,
    ScheduledEventPrivacyLevel,
    Weekday,
)
from bostie.testing import FakeDiscord, payloads, run, running


def test_an_event_goes_out_with_only_the_recurrence_fields_set() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        event = payloads.fixture("events/GUILD_SCHEDULED_EVENT_CREATE")
        start = datetime(2026, 11, 4, 18, tzinfo=UTC)
        async with running(bot, fake):
            fake.fail("POST", "/guilds/1/scheduled-events", 200, event)
            await bot.rest.scheduled_events.create(
                1,
                name="game night",
                privacy_level=ScheduledEventPrivacyLevel.GUILD_ONLY,
                entity_type=ScheduledEventEntityType.EXTERNAL,
                entity_metadata=ScheduledEventEntityMetadata(location="the park"),
                scheduled_start_time=start,
                scheduled_end_time=start.replace(hour=20),
                recurrence_rule=RecurrenceRule(
                    start=start,
                    frequency=RecurrenceFrequency.WEEKLY,
                    interval=1,
                    by_weekday=(Weekday.WEDNESDAY,),
                ),
            )
            assert fake.requests[-1].json == {
                "name": "game night",
                "privacy_level": 2,
                "entity_type": 3,
                "entity_metadata": {"location": "the park"},
                "scheduled_start_time": "2026-11-04T18:00:00Z",
                "scheduled_end_time": "2026-11-04T20:00:00Z",
                "recurrence_rule": {
                    "start": "2026-11-04T18:00:00Z",
                    "frequency": 2,
                    "interval": 1,
                    "by_weekday": [2],
                },
            }

    run(scenario)
