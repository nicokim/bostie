import logging

import msgspec

from bostie.events import Event

log = logging.getLogger("bostie.events")

_undecodable: set[str] = set()


def decode_event[E: Event](event: type[E], shard_id: int, data: bytes) -> E | None:
    """Decode a dispatch, or return None and log a warning when its shape changed.

    The warning is logged once per event name; `RawEvent` listeners still run.
    """
    try:
        return event._decode(shard_id, data)
    except msgspec.ValidationError as error:
        level = logging.DEBUG if event.NAME in _undecodable else logging.WARNING
        _undecodable.add(event.NAME)
        log.log(
            level,
            "%s has an unexpected shape (%s); typed listeners skip it",
            event.NAME,
            error,
        )
        return None
