from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bostie._bridge import Bridge
from bostie.errors import StateError
from bostie.models import Member, Presence
from bostie.models.base import encoder
from bostie.snowflake import shard_of

if TYPE_CHECKING:
    import asyncio

    from bostie._gateway import GatewaySession

_PRESENCE_UPDATE = 3
_LARGE_THRESHOLD = range(50, 251)


@dataclass(frozen=True, slots=True)
class RequestedMembers:
    """The answer to `bot.request_members()`; `not_found` holds unknown user ids."""

    members: tuple[Member, ...]
    presences: tuple[Presence, ...]
    not_found: tuple[int, ...]


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
    """The bot's gateway shards: send commands, reconnect, and measure latency."""

    def __init__(
        self,
        bridge: Bridge,
        token: str,
        *,
        intents: int,
        shard_count: int,
        shard_ids: Iterable[int],
        large_threshold: int = _LARGE_THRESHOLD.start,
        session_file: Path | None = None,
    ) -> None:
        self._bridge = bridge
        self._token = token
        self._intents = intents
        self._large_threshold = large_threshold
        self._session_file = session_file
        self._resuming: dict[int, GatewaySession] = {}
        self._saved: dict[int, GatewaySession] = {}
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
        return shard_of(guild_id, self.shard_count)

    def send(self, op: int, d: Any = None) -> None:
        """Send a gateway command to the shards it belongs to.

        Op 3 goes to every shard, a payload with a guild_id to that guild's shard,
        one with guild_ids to each of their shards with its share of the ids, and
        anything else to shard 0. Replies arrive only for event names someone
        listens to.
        """
        if op == _PRESENCE_UPDATE:
            for shard_id in self._running:
                self._shards[shard_id].send(op, d)
            return
        match d:
            case {"guild_id": guild_id}:
                self.shard(self.shard_for(int(guild_id))).send(op, d)
            case {"guild_ids": [*guild_ids]}:
                shares: dict[int, list[Any]] = {}
                for guild_id in guild_ids:
                    shares.setdefault(self.shard_for(int(guild_id)), []).append(
                        guild_id
                    )
                for shard_id, share in shares.items():
                    self.shard(shard_id).send(op, {**d, "guild_ids": share})
            case _:
                self.shard(0).send(op, d)

    def reconnect(self) -> None:
        """Drop every shard's connection and resume its session on a new one."""
        for shard_id in self._running:
            self._shards[shard_id].reconnect()

    @property
    def latency(self) -> float | None:
        """Mean seconds from a heartbeat to its ACK over shards that measured one."""
        measured = [
            latency
            for shard in self._shards.values()
            if (latency := shard.latency) is not None
        ]
        return sum(measured) / len(measured) if measured else None
