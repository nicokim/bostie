import asyncio
import contextlib
import logging
import secrets
import sys
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import msgspec

from bostie._aio import spawn
from bostie._bridge import StatusDelivery
from bostie._util import UNSET, Unset
from bostie.activity import ActivityDict
from bostie.enums import Intents, Status
from bostie.errors import (
    BostieError,
    GatewayClosed,
    InvalidToken,
    MemberRequestRateLimited,
    MissingIntent,
    PrivilegedIntentsRequired,
    ShardingRequired,
    StateError,
)
from bostie.gateway import _PRESENCE_UPDATE, GatewayClient
from bostie.models import Guild, UnavailableGuild
from bostie.models.base import decoder, encoder

CLOSE_TIMEOUT = 2.0
REQUEST_GUILD_MEMBERS = 8
REQUEST_SOUNDBOARD_SOUNDS = 31
REQUEST_CHANNEL_INFO = 43
REQUEST_TIMEOUT = 30.0
MAX_REQUESTED_MEMBERS = 100
BUG_CLOSE_CODES = frozenset({4001, 4002, 4005, 4008})
AUTHENTICATION_FAILED = 4004
CLOSE_ERRORS: dict[int, type[GatewayClosed]] = {
    4011: ShardingRequired,
    4014: PrivilegedIntentsRequired,
}

log = logging.getLogger("bostie.gateway")


def presence_fields(
    *,
    status: Status | None,
    activities: Iterable[ActivityDict] | None,
    afk: bool | None,
    idle_since: datetime | Unset | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if idle_since is not UNSET:
        fields["since"] = (
            None if idle_since is None else int(idle_since.timestamp() * 1000)
        )
    if activities is not None:
        fields["activities"] = list(activities)
    if status is not None:
        fields["status"] = status
    if afk is not None:
        fields["afk"] = afk
    return fields


class GatewaySession(msgspec.Struct, frozen=True, kw_only=True):
    """What a later process needs to resume a shard's session."""

    shard_id: int
    shard_count: int
    session_id: str
    seq: int
    resume_url: str


def member_request(
    guild_id: int,
    intents: Intents,
    *,
    query: str | None,
    limit: int | None,
    user_ids: Iterable[int] | None,
    presences: bool,
) -> dict[str, Any]:
    if presences and not intents & Intents.GUILD_PRESENCES:
        raise MissingIntent(
            "presences need Intents.GUILD_PRESENCES; add it to Bot(intents=...)"
        )
    request: dict[str, Any] = {
        "guild_id": str(guild_id),
        "presences": presences,
        "nonce": secrets.token_hex(16),
    }
    if user_ids is not None:
        if query is not None or limit is not None:
            raise ValueError("pass user_ids, or query and limit, not both")
        request["user_ids"] = [str(user_id) for user_id in user_ids]
        if len(request["user_ids"]) > MAX_REQUESTED_MEMBERS:
            raise ValueError("user_ids takes at most 100 users")
        return request
    request["query"] = "" if query is None else query
    request["limit"] = 0 if limit is None else limit
    if not 0 <= request["limit"] <= MAX_REQUESTED_MEMBERS:
        raise ValueError(f"limit must be 0 to 100, got {request['limit']}")
    if not request["query"] and not intents & Intents.GUILD_MEMBERS:
        raise MissingIntent(
            "requesting every member needs Intents.GUILD_MEMBERS; "
            "add it to Bot(intents=...)"
        )
    return request


class Shards(GatewayClient):
    """The bot's controls over its shards: start, identify, filter and close."""

    def identify_limit(self, session_start_limit: Mapping[str, Any]) -> None:
        self._bridge.core.gateway_identify_limit(
            session_start_limit["remaining"],
            session_start_limit["total"],
            session_start_limit["reset_after"] / 1000,
            session_start_limit["max_concurrency"],
        )

    def gate_identifies(self) -> None:
        self._bridge.core.gateway_gate_identifies()

    def approve_identify(self, shard_id: int, request: int) -> None:
        with contextlib.suppress(StateError):
            self._bridge.core.gateway_approve_identify(shard_id, request)

    def load_sessions(self) -> frozenset[int]:
        path = self._session_file
        if path is None or not path.exists():
            return frozenset()
        data = path.read_bytes()
        path.unlink()
        try:
            sessions = decoder(tuple[GatewaySession, ...]).decode(data)
        except msgspec.DecodeError:
            log.warning("%s holds no gateway sessions, so every shard identifies", path)
            return frozenset()
        self._resuming = {
            session.shard_id: session
            for session in sessions
            if session.shard_count == self.shard_count
            and session.shard_id in self._shards
        }
        return frozenset(self._resuming)

    async def start(self, url: str, presence: Mapping[str, Any]) -> None:
        stopped = self._bridge.create_future()
        for shard_id in self._shards:
            identify = self._identify(shard_id, presence)
            saved = self._resuming.pop(shard_id, None)
            resume = (
                None
                if saved is None
                else (saved.session_id, saved.seq, saved.resume_url)
            )
            self._bridge.core.gateway_start(shard_id, url, identify, resume)
            self._running.add(shard_id)
        self._stopped = stopped
        await asyncio.shield(stopped)

    def update_presence(self, presence: Mapping[str, Any]) -> None:
        for shard_id in self._running:
            identify = self._identify(shard_id, presence)
            self._bridge.core.gateway_identify(shard_id, identify)
            self._shards[shard_id].send(_PRESENCE_UPDATE, presence)

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
                "large_threshold": self._large_threshold,
                "shard": [shard_id, self.shard_count],
                "presence": presence,
            }
        )

    def filter(self, names: Iterable[str] | None) -> None:
        self._bridge.core.gateway_filter(None if names is None else list(names))

    async def close(self) -> None:
        """Close every shard and wait up to 2 s for them to end.

        Without a session file they close with code 1000, which ends their
        sessions; with one, they keep them and the file saves them.
        """
        core = self._bridge.core
        close = (
            core.gateway_close if self._session_file is None else core.gateway_suspend
        )
        for shard_id in self._running:
            with contextlib.suppress(StateError):
                close(shard_id)
        stopped = self._stopped
        if stopped is not None and not stopped.done():
            await asyncio.wait([stopped], timeout=CLOSE_TIMEOUT)
            if not stopped.done():
                stopped.set_result(None)
        if self._session_file is not None and self._saved:
            self._session_file.write_bytes(encoder.encode(list(self._saved.values())))

    def handle(self, delivery: StatusDelivery) -> None:
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
            case ("stopped", shard_id, code, reason, session):
                self._running.discard(shard_id)
                if session is not None:
                    session_id, seq, resume_url = session
                    self._saved[shard_id] = GatewaySession(
                        shard_id=shard_id,
                        shard_count=self.shard_count,
                        session_id=session_id,
                        seq=seq,
                        resume_url=resume_url,
                    )
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


def _close_error(code: int, reason: str) -> BostieError:
    if code == AUTHENTICATION_FAILED:
        return InvalidToken(
            f"the gateway closed the connection with code 4004: {reason}"
        )
    return CLOSE_ERRORS.get(code, GatewayClosed)(code, reason)


class _GuildPresence(msgspec.Struct):
    id: int
    unavailable: bool = False
    large: bool = False


class _ReadyGuilds(msgspec.Struct):
    guilds: tuple[_GuildPresence, ...] = ()


def _presence(guild: Guild | UnavailableGuild) -> _GuildPresence:
    large = isinstance(guild, Guild) and bool(guild.large)
    return _GuildPresence(guild.id, unavailable=guild.unavailable, large=large)


def _decoded[S: msgspec.Struct](cls: type[S], data: bytes) -> S | None:
    try:
        decoded = decoder(cls).decode(data)
    except msgspec.ValidationError:
        return None
    return decoded


@dataclass(slots=True)
class _ShardGuilds:
    known: set[int] = field(default_factory=set[int])
    pending: set[int] = field(default_factory=set[int])
    chunking: set[int] = field(default_factory=set[int])
    queued: set[int] = field(default_factory=set[int])
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation: int = 0
    last: float = 0.0
    timer: asyncio.TimerHandle | None = None
    session: bool = False
    renewing: bool = False


class Readiness:
    """A shard is ready once its READY's guilds arrive, or none for `timeout` s.

    With `chunk`, a guild whose GUILD_CREATE leaves members out arrives once
    `chunk(guild_id)` has asked for all of them, one guild at a time per shard.
    """

    def __init__(
        self,
        shard_ids: Iterable[int],
        on_ready: Callable[[int], object],
        *,
        guilds: bool,
        timeout: float,
        tasks: set[asyncio.Task[None]],
        chunk: Callable[[int], Awaitable[object]] | None = None,
        small_guilds_complete: bool = False,
    ) -> None:
        self._shards = {shard_id: _ShardGuilds() for shard_id in shard_ids}
        self._on_ready = on_ready
        self._guilds = guilds
        self._timeout = timeout
        self._tasks = tasks
        self._chunk = chunk
        self._small_guilds_complete = small_guilds_complete

    def renewing(self, shard_id: int) -> bool:
        """Whether the shard lost its session and has no READY for a new one yet."""
        shard = self._shards.get(shard_id)
        return shard is not None and shard.renewing

    def ready(self, shard_id: int, data: bytes) -> bool:
        """Start following a READY; True when it leaves no guild to wait for."""
        shard = self._shards[shard_id]
        ready = _decoded(_ReadyGuilds, data)
        ids = set[int]() if ready is None else {guild.id for guild in ready.guilds}
        shard.known = ids
        shard.pending = set(ids) if self._guilds else set()
        shard.chunking.clear()
        shard.queued.clear()
        shard.generation += 1
        shard.session = True
        shard.renewing = False
        self._cancel(shard)
        if not shard.pending:
            return True
        shard.last = asyncio.get_running_loop().time()
        self._wait(shard_id, self._timeout)
        return False

    def created(
        self, shard_id: int, data: bytes, decoded: Guild | UnavailableGuild | None
    ) -> tuple[bool, bool]:
        """Note a GUILD_CREATE: whether the bot joined, and the shard is now ready."""
        guild = (
            _decoded(_GuildPresence, data) if decoded is None else _presence(decoded)
        )
        shard = self._shards[shard_id]
        if guild is None:
            return False, False
        joined = not guild.unavailable and guild.id not in shard.known
        shard.known.add(guild.id)
        chunk = self._chunk
        if chunk is None or guild.unavailable or not self._lacks_members(guild):
            return joined, self._arrived(shard, guild.id)
        if guild.id in shard.pending:
            shard.pending.discard(guild.id)
            shard.chunking.add(guild.id)
            shard.last = asyncio.get_running_loop().time()
        shard.queued.add(guild.id)
        spawn(
            self._tasks,
            self._chunk_guild(shard_id, guild.id, shard.generation, chunk),
        )
        return joined, False

    def deleted(self, shard_id: int, data: bytes) -> bool:
        """Note a GUILD_DELETE; True when the shard is now ready."""
        guild = _decoded(_GuildPresence, data)
        shard = self._shards[shard_id]
        if guild is None:
            return False
        if guild.unavailable:
            shard.known.add(guild.id)
        else:
            shard.known.discard(guild.id)
        return self._arrived(shard, guild.id)

    def resumed(self, shard_id: int) -> bool:
        """Note a RESUMED; True when it resumed a session from before `start()`."""
        shard = self._shards[shard_id]
        if shard.session:
            return False
        shard.session = True
        return True

    def ended(self, shard_id: int) -> bool:
        """Note a session that ended; True when the shard had one to lose."""
        shard = self._shards[shard_id]
        self._cancel(shard)
        shard.renewing = shard.session
        return shard.session

    def close(self) -> None:
        for shard in self._shards.values():
            self._cancel(shard)

    def _lacks_members(self, guild: _GuildPresence) -> bool:
        return guild.large or not self._small_guilds_complete

    async def _chunk_guild(
        self,
        shard_id: int,
        guild_id: int,
        generation: int,
        chunk: Callable[[int], Awaitable[object]],
    ) -> None:
        shard = self._shards[shard_id]
        async with shard.lock:
            if shard.generation != generation or guild_id not in shard.queued:
                return
            shard.queued.discard(guild_id)
            if not await _request_all(chunk, guild_id):
                return
        if shard.generation == generation and guild_id in shard.chunking:
            shard.chunking.discard(guild_id)
            if self._settled(shard):
                self._on_ready(shard_id)

    def _arrived(self, shard: _ShardGuilds, guild_id: int) -> bool:
        if guild_id not in shard.pending:
            return False
        shard.pending.discard(guild_id)
        shard.last = asyncio.get_running_loop().time()
        return self._settled(shard)

    def _settled(self, shard: _ShardGuilds) -> bool:
        if shard.pending or shard.chunking:
            return False
        self._cancel(shard)
        return True

    def _wait(self, shard_id: int, delay: float) -> None:
        loop = asyncio.get_running_loop()
        self._shards[shard_id].timer = loop.call_later(delay, self._check, shard_id)

    def _check(self, shard_id: int) -> None:
        shard = self._shards[shard_id]
        shard.timer = None
        left = shard.last + self._timeout - asyncio.get_running_loop().time()
        if left > 0:
            self._wait(shard_id, left)
        elif shard.pending:
            shard.pending.clear()
            if not shard.chunking:
                self._on_ready(shard_id)

    @staticmethod
    def _cancel(shard: _ShardGuilds) -> None:
        if shard.timer is not None:
            shard.timer.cancel()
            shard.timer = None


async def _request_all(
    chunk: Callable[[int], Awaitable[object]], guild_id: int
) -> bool:
    while True:
        try:
            await chunk(guild_id)
        except MemberRequestRateLimited as limited:
            await asyncio.sleep(limited.retry_after)
        except StateError:
            return False
        except TimeoutError:
            log.warning(
                "the members of guild %d stopped arriving; the cache lacks some",
                guild_id,
            )
            return True
        else:
            return True
