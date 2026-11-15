import json
from pathlib import Path

import pytest

from bostie import decode_webhook_event
from bostie.events import (
    ApplicationAuthorized,
    ApplicationDeauthorized,
    EntitlementCreate,
    Event,
    GameDirectMessageCreate,
    GameDirectMessageDelete,
    GameDirectMessageUpdate,
    LobbyMessageCreate,
    LobbyMessageDelete,
    LobbyMessageUpdate,
    QuestUserEnrollment,
    RawEvent,
)

FIXTURES = Path(__file__).parent / "fixtures" / "webhook_events"


def payload(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


@pytest.mark.parametrize(
    "event_type",
    [
        ApplicationAuthorized,
        ApplicationDeauthorized,
        EntitlementCreate,
        QuestUserEnrollment,
        LobbyMessageCreate,
        LobbyMessageUpdate,
        LobbyMessageDelete,
        GameDirectMessageCreate,
        GameDirectMessageUpdate,
        GameDirectMessageDelete,
    ],
)
def test_every_documented_webhook_event_decodes_to_its_class(
    event_type: type[Event],
) -> None:
    body = payload(event_type.NAME)

    event = decode_webhook_event(body)

    assert type(event) is event_type
    assert event.raw == json.loads(body)["event"]["data"]


def test_a_ping_has_no_event_and_an_unmodelled_type_comes_raw() -> None:
    future = {"type": 1, "event": {"type": "BOSTIE_FUTURE", "data": {"a": 1}}}

    assert decode_webhook_event(payload("PING")) is None
    event = decode_webhook_event(json.dumps(future).encode())
    assert isinstance(event, RawEvent)
    assert (event.name, event.raw) == ("BOSTIE_FUTURE", {"a": 1})
