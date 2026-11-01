import asyncio
import contextlib
import logging
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from bostie._bridge import Bridge, StatusDelivery
from bostie.errors import (
    BostieError,
    GatewayClosed,
    InvalidToken,
    PrivilegedIntentsRequired,
    ShardingRequired,
)
from bostie.models.base import encoder

SHARD = 0
CLOSE_TIMEOUT = 2.0
BUG_CLOSE_CODES = frozenset({4001, 4002, 4005, 4008})
AUTHENTICATION_FAILED = 4004
CLOSE_ERRORS: dict[int, type[GatewayClosed]] = {
    4011: ShardingRequired,
    4014: PrivilegedIntentsRequired,
}

log = logging.getLogger("bostie.gateway")


class GatewayClient:
    """The bot's gateway connection: identify, send commands, reconnect and close."""

    def __init__(self, bridge: Bridge, token: str) -> None:
        self._bridge = bridge
        self._token = token
        self._stopped: asyncio.Future[None] | None = None

    def _identify_limit(self, session_start_limit: Mapping[str, Any]) -> None:
        self._bridge.core.gateway_identify_limit(
            session_start_limit["remaining"],
            session_start_limit["total"],
            session_start_limit["reset_after"] / 1000,
        )

    async def _start(
        self,
        url: str,
        *,
        intents: int,
        presence: Mapping[str, Any] | None = None,
    ) -> None:
        identify: dict[str, Any] = {
            "token": self._token,
            "intents": intents,
            "properties": {
                "os": sys.platform,
                "browser": "bostie",
                "device": "bostie",
            },
            "shard": [SHARD, 1],
        }
        if presence is not None:
            identify["presence"] = presence
        stopped = self._bridge.create_future()
        self._bridge.core.gateway_start(SHARD, url, encoder.encode(identify))
        self._stopped = stopped
        await asyncio.shield(stopped)

    def send(self, op: int, d: Any = None) -> None:
        """Send a gateway command such as op 8, 31 or 43.

        Their replies arrive only for event names someone listens to.
        """
        self._bridge.core.gateway_send(SHARD, encoder.encode({"op": op, "d": d}))

    def _filter(self, names: Iterable[str] | None) -> None:
        self._bridge.core.gateway_filter(None if names is None else list(names))

    def reconnect(self) -> None:
        """Drop the connection and resume the session on a new one."""
        self._bridge.core.gateway_reconnect(SHARD)

    async def close(self) -> None:
        """Close the connection with code 1000 and wait up to 2 s for it to end."""
        stopped = self._stopped
        if stopped is None or stopped.done():
            return
        with contextlib.suppress(RuntimeError):
            self._bridge.core.gateway_close(SHARD)
        await asyncio.wait([stopped], timeout=CLOSE_TIMEOUT)
        if not stopped.done():
            stopped.set_result(None)

    @property
    def latency(self) -> float | None:
        """Seconds from the last heartbeat to its ACK, or None when there is none."""
        try:
            return self._bridge.core.gateway_latency(SHARD)
        except RuntimeError:
            return None

    def _handle(self, delivery: StatusDelivery) -> None:
        match delivery:
            case ("disconnected", shard_id, code, reason, resuming):
                cause = reason if code is None else f"{reason} (close code {code})"
                log.log(
                    logging.INFO if resuming else logging.WARNING,
                    "shard %d disconnected: %s; %s",
                    shard_id,
                    cause,
                    "resuming" if resuming else "starting a new session",
                )
                if code in BUG_CLOSE_CODES:
                    log.warning(
                        "close code %d (%s) means Discord refused what bostie sent; "
                        "this is likely a bostie bug, please report it",
                        code,
                        reason,
                    )
            case ("stopped", _, code, reason):
                if self._stopped is not None and not self._stopped.done():
                    if code is None:
                        self._stopped.set_result(None)
                    else:
                        self._stopped.set_exception(_close_error(code, reason))
            case ("backpressure", shard_id, queued_bytes):
                log.warning(
                    "shard %d paused reading: %d bytes of events are waiting because "
                    "the event loop is not keeping up",
                    shard_id,
                    queued_bytes,
                )
            case ("invalid_requests", count):
                log.warning(
                    "%d invalid requests (401, 403, 429) in the last 10 minutes; "
                    "Discord bans the IP at 10 000",
                    count,
                )


def _close_error(code: int, reason: str) -> BostieError:
    if code == AUTHENTICATION_FAILED:
        return InvalidToken(
            f"the gateway closed the connection with code 4004: {reason}"
        )
    return CLOSE_ERRORS.get(code, GatewayClosed)(code, reason)
