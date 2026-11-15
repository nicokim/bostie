from datetime import UTC, datetime

import msgspec
import pytest

from bostie import Emoji, Member, Message, snowflake_time, time_snowflake
from bostie.events import MessageDelete, decode_event
from bostie.models import decode
from bostie.testing import payloads


def encoded(name: str) -> bytes:
    return msgspec.json.encode(payloads.fixture(name))


def test_an_id_gives_its_creation_time_and_a_time_the_ids_around_it() -> None:
    created = snowflake_time(175928847299117063)

    assert created == datetime(2016, 4, 30, 11, 18, 25, 796000, tzinfo=UTC)
    assert time_snowflake(created) == 175928847298985984
    assert time_snowflake(created, high=True) == 175928847303180287


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            decode(Message, encoded("rest/message")),
            datetime(2026, 10, 27, 12, 0, 5, 456000, tzinfo=UTC),
        ),
        (
            decode(Member, encoded("rest/member")),
            datetime(2021, 3, 14, 21, 45, tzinfo=UTC),
        ),
        (
            decode_event(MessageDelete, 0, encoded("events/MESSAGE_DELETE")),
            datetime(2026, 10, 27, 12, 0, 0, 123000, tzinfo=UTC),
        ),
        (decode(Emoji, b'{"id": null, "name": "\\ud83d\\udd25"}'), None),
    ],
    ids=["message", "member", "event", "unicode emoji"],
)
def test_models_with_an_id_tell_when_they_were_created(
    model: Message | Member | MessageDelete | Emoji | None,
    expected: datetime | None,
) -> None:
    assert model is not None
    assert model.created_at == expected
