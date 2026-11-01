import asyncio
import contextlib
import logging
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, TypedDict

from bostie._bridge import Bridge, StatusDelivery
from bostie.activity import ActivityDict
from bostie.enums import Status
from bostie.errors import (
    BostieError,
    GatewayClosed,
    InvalidToken,
    PrivilegedIntentsRequired,
    ShardingRequired,
    StateError,
)
from bostie.models.base import encoder

CLOSE_TIMEOUT = 2.0
PRESENCE_UPDATE = 3
BUG_CLOSE_CODES = frozenset({4001, 4002, 4005, 4008})
AUTHENTICATION_FAILED = 4004
CLOSE_ERRORS: dict[int, type[GatewayClosed]] = {
    4011: ShardingRequired,
    4014: PrivilegedIntentsRequired,
}

log = logging.getLogger("bostie.gateway")


class PresenceOptions(TypedDict, total=False):
    """What the bot shows; `bot.update_presence()` keeps each option not given."""

    status: Status
    activities: Iterable[ActivityDict]
    afk: bool
    idle_since: datetime | None


PRESENCE_OPTIONS = PresenceOptions.__optional_keys__


def presence_fields(options: PresenceOptions) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "idle_since" in options:
        since = options["idle_since"]
        fields["since"] = None if since is None else int(since.timestamp() * 1000)
    if "activities" in options:
        fields["activities"] = list(options["activities"])
    if "status" in options:
        fields["status"] = options["status"]
    if "afk" in options:
        fields["afk"] = options["afk"]
    return fields


class Shard:
    """One gateway connection of the bot."""

    def __init__(self, bridge: Bridge, shard_id: int) -> None:
        self._bridge = bridge
        self.id = shard_id

    def send(self, op: int, d: Any = None) -> None:
        """Send a gateway command on this shard."""
        self._bridge.core.gateway_send(self.id, encoder.encode({"op": op, "d": d}))

    def reconnect(self) -> None:
        """Drop the connection and resume the session on a new one."""
        self._bridge.core.gateway_reconnect(self.id)

    @property
    def latency(self) -> float | None:
        """Seconds from the last heartbeat to its ACK, or None when there is none."""
        try:
            return self._bridge.core.gateway_latency(self.id)
        except StateError:
            return None


class GatewayClient:
    """The bot's gateway shards: identify, route commands, reconnect and close."""

    def __init__(
        self,
        bridge: Bridge,
        token: str,
        *,
        intents: int,
        shard_count: int,
        shard_ids: Iterable[int],
    ) -> None:
        self._bridge = bridge
        self._token = token
        self._intents = intents
        self.shard_count = shard_count
        self._shards = {shard_id: Shard(bridge, shard_id) for shard_id in shard_ids}
        self._running: set[int] = set()
        self._stopped: asyncio.Future[None] | None = None

    @property
    def shard_ids(self) -> tuple[int, ...]:
        """The shards this process runs."""
        return tuple(self._shards)

    def shard(self, shard_id: int) -> Shard:
        """The shard with this id; ValueError when this process does not run it."""
        try:
            return self._shards[shard_id]
        except KeyError:
            raise ValueError(f"shard {shard_id} does not run here") from None

    def shard_for(self, guild_id: int) -> int:
        """The shard Discord sends this guild's events to."""
        return (guild_id >> 22) % self.shard_count

    def _identify_limit(self, session_start_limit: Mapping[str, Any]) -> None:
        self._bridge.core.gateway_identify_limit(
            session_start_limit["remaining"],
            session_start_limit["total"],
            session_start_limit["reset_after"] / 1000,
            session_start_limit["max_concurrency"],
        )

    async def _start(self, url: str, presence: Mapping[str, Any]) -> None:
        stopped = self._bridge.create_future()
        for shard_id in self._shards:
            identify = self._identify(shard_id, presence)
            self._bridge.core.gateway_start(shard_id, url, identify)
            self._running.add(shard_id)
        self._stopped = stopped
        await asyncio.shield(stopped)

    def _update_presence(self, presence: Mapping[str, Any]) -> None:
        for shard_id in self._running:
            identify = self._identify(shard_id, presence)
            self._bridge.core.gateway_identify(shard_id, identify)
            self._shards[shard_id].send(PRESENCE_UPDATE, presence)

    def _identify(self, shard_id: int, presence: Mapping[str, Any]) -> bytes:
        return encoder.encode(
            {
                "token": self._token,
                "intents": self._intents,
                "properties": {
                    "os": sys.platform,
                    "browser": "bostie",
                    "device": "bostie",
                },
                "shard": [shard_id, self.shard_count],
                "presence": presence,
            }
        )

    def send(self, op: int, d: Any = None) -> None:
        """Send a gateway command such as op 31 or 43 to the shard it belongs to.

        Op 3 goes to every shard, a payload with a guild_id to that guild's shard,
        anything else to shard 0. Replies arrive only for event names someone
        listens to.
        """
        if op == PRESENCE_UPDATE:
            for shard_id in self._running:
                self._shards[shard_id].send(op, d)
            return
        match d:
            case {"guild_id": guild_id}:
                self.shard(self.shard_for(int(guild_id))).send(op, d)
            case _:
                self.shard(0).send(op, d)

    def _filter(self, names: Iterable[str] | None) -> None:
        self._bridge.core.gateway_filter(None if names is None else list(names))

    def reconnect(self) -> None:
        """Drop every shard's connection and resume its session on a new one."""
        for shard_id in self._running:
            self._shards[shard_id].reconnect()

    async def close(self) -> None:
        """Close every shard with code 1000 and wait up to 2 s for them to end."""
        for shard_id in self._running:
            with contextlib.suppress(StateError):
                self._bridge.core.gateway_close(shard_id)
        stopped = self._stopped
        if stopped is None or stopped.done():
            return
        await asyncio.wait([stopped], timeout=CLOSE_TIMEOUT)
        if not stopped.done():
            stopped.set_result(None)

    @property
    def latency(self) -> float | None:
        """Mean seconds from a heartbeat to its ACK over shards that measured one."""
        measured = [
            latency
            for shard in self._shards.values()
            if (latency := shard.latency) is not None
        ]
        return sum(measured) / len(measured) if measured else None

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
            case ("stopped", shard_id, code, reason):
                self._running.discard(shard_id)
                stopped = self._stopped
                if stopped is None or stopped.done():
                    return
                if code is not None:
                    stopped.set_exception(_close_error(code, reason))
                elif not self._running:
                    stopped.set_result(None)
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
