import logging
import os
from collections.abc import Mapping

from bostie import decode_webhook_event, verify_signature
from bostie.events import ApplicationDeauthorized, RawEvent

log = logging.getLogger("my-server")
PUBLIC_KEY = os.environ.get("DISCORD_PUBLIC_KEY", "")


def webhook_event(headers: Mapping[str, str], body: bytes) -> int:
    signature = headers.get("x-signature-ed25519", "")
    timestamp = headers.get("x-signature-timestamp", "")
    if not verify_signature(PUBLIC_KEY, signature, timestamp, body):
        return 401
    event = decode_webhook_event(body)
    if isinstance(event, ApplicationDeauthorized):
        log.info("%s removed the app", event.user.username)
    elif isinstance(event, RawEvent):
        log.info("an event bostie does not model: %s", event.name)
    return 204
