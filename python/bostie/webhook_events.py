import msgspec

from bostie import _native
from bostie.events import (
    ApplicationAuthorized,
    ApplicationDeauthorized,
    EntitlementCreate,
    EntitlementDelete,
    EntitlementUpdate,
    Event,
    GameDirectMessageCreate,
    GameDirectMessageDelete,
    GameDirectMessageUpdate,
    LobbyMessageCreate,
    LobbyMessageDelete,
    LobbyMessageUpdate,
    QuestUserEnrollment,
    RawEvent,
    decode_event,
)
from bostie.models.base import decoder

_WEBHOOK_EVENTS: dict[str, type[Event]] = {
    event.NAME: event
    for event in (
        ApplicationAuthorized,
        ApplicationDeauthorized,
        EntitlementCreate,
        EntitlementUpdate,
        EntitlementDelete,
        QuestUserEnrollment,
        LobbyMessageCreate,
        LobbyMessageUpdate,
        LobbyMessageDelete,
        GameDirectMessageCreate,
        GameDirectMessageUpdate,
        GameDirectMessageDelete,
    )
}


class _EventBody(msgspec.Struct):
    type: str
    data: msgspec.Raw = msgspec.Raw(b"null")


class _Payload(msgspec.Struct):
    event: _EventBody | None = None


def verify_signature(
    public_key: str, signature: str, timestamp: str, body: bytes
) -> bool:
    """Check an X-Signature-Ed25519 header against the app's hex `public_key`."""
    return _native.verify_signature(public_key, signature, timestamp, body)


def decode_webhook_event(body: bytes) -> Event | None:
    """A webhook event request's event, a RawEvent if untyped, or None for a PING."""
    named = _split_webhook_event(body)
    if named is None:
        return None
    name, data = named
    cls = _WEBHOOK_EVENTS.get(name)
    decoded = None if cls is None else decode_event(cls, 0, data)
    return decoded or RawEvent(raw_json=data, name=name, seq=0)


def _split_webhook_event(body: bytes) -> tuple[str, bytes] | None:
    """The type and data of a webhook event request's event; None for a PING."""
    event = decoder(_Payload).decode(body).event
    return None if event is None else (event.type, bytes(event.data))
