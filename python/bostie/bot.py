import asyncio
import contextlib
import logging
import os
import signal
import threading
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
)
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self, cast, override

import msgspec

from bostie import fmt
from bostie._aio import spawn
from bostie._bridge import Bridge, GatewayDelivery, warn_invalid_requests
from bostie._dispatch import Claim, Interactions
from bostie._gateway import (
    REQUEST_CHANNEL_INFO,
    REQUEST_GUILD_MEMBERS,
    REQUEST_SOUNDBOARD_SOUNDS,
    REQUEST_TIMEOUT,
    Readiness,
    Shards,
    member_request,
    presence_fields,
)
from bostie._sync import SyncResult, Translate, fetch_ids, sync_commands
from bostie._util import UNSET, Unset, qualified_name, resolve_token
from bostie.activity import ActivityDict
from bostie.cache import Cache
from bostie.commands import _CommandDefaults
from bostie.context import (
    ComponentContext,
    ModalContext,
)
from bostie.enums import (
    ApplicationIntegrationType,
    Intents,
    InteractionContextType,
    MembershipState,
    Status,
    TeamMemberRole,
)
from bostie.errors import (
    Forbidden,
    MemberRequestRateLimited,
    MissingIntent,
    StateError,
    TransportError,
)
from bostie.events import (
    EVENTS,
    BotEvent,
    ChannelInfo,
    Event,
    GatewayRateLimited,
    GuildCreate,
    GuildDelete,
    InteractionCreate,
    ListenerError,
    MembersChunk,
    RawEvent,
    Ready,
    Resumed,
    ShardBackpressure,
    ShardDisconnect,
    ShardReady,
    ShardStop,
    SoundboardSounds,
    Started,
    Starting,
    Stopped,
    Stopping,
    TaskError,
    UserUpdate,
    decode_event,
)
from bostie.gateway import _LARGE_THRESHOLD, GatewayClient, RequestedMembers
from bostie.http import DEFAULT_HTTP, HTTPClient, HTTPSettings
from bostie.lookup import Lookup
from bostie.models import (
    AllowedMentions,
    Application,
    CurrentUser,
    SoundboardSound,
    VoiceChannelInfo,
)
from bostie.models.message import DEFAULT_ALLOWED_MENTIONS
from bostie.rest import REST
from bostie.router import _RAW_EVENT_NAME, AnyEvent, Listener, Router
from bostie.tasks import Task
from bostie.voice.connection import VoiceManager
from bostie.voice.events import VoiceEvent
from bostie.voice.formats import Defaults

_log = logging.getLogger("bostie.bot")

_TASK_CANCEL_TIMEOUT = 5.0
_CHANNEL_INFO_FIELDS: tuple[Literal["status", "voice_start_time"], ...] = (
    "status",
    "voice_start_time",
)
DEFAULT_VOICE = Defaults()
_OWNER_ROLES = frozenset({TeamMemberRole.ADMIN, TeamMemberRole.DEVELOPER})
type _LocalEvent = VoiceEvent | BotEvent


@dataclass(slots=True)
class _Waiter[E: AnyEvent]:
    event: type[E]
    check: Callable[[E], bool] | None
    queue: asyncio.Queue[E | Exception]
    once: bool
    closed: bool = False


@dataclass(slots=True)
class _Request:
    shard_id: int
    answers: asyncio.Queue[Event | Exception] = field(
        default_factory=asyncio.Queue[Event | Exception]
    )


@dataclass(slots=True)
class _Dispatch:
    shard_id: int
    seq: int
    name: str
    data: bytes
    decoded: dict[type[Event], Event | None] = field(
        default_factory=dict[type[Event], Event | None]
    )

    def event[E: Event](self, cls: type[E]) -> E | None:
        if cls not in self.decoded:
            if cls is RawEvent:
                self.decoded[cls] = RawEvent(
                    shard_id=self.shard_id,
                    raw_json=self.data,
                    name=self.name,
                    seq=self.seq,
                )
            else:
                self.decoded[cls] = decode_event(cls, self.shard_id, self.data)
        return cast("E | None", self.decoded[cls])


class Bot(Router):
    """A Discord bot: commands, components, listeners, REST and its gateway shards."""

    user: CurrentUser | None = None
    application_id: int | None = None
    _install_types: tuple[ApplicationIntegrationType, ...] = ()
    _bridge: Bridge | None = None
    _http: HTTPClient | None = None
    _rest: REST | None = None
    _shards: Shards | None = None
    _closing: asyncio.Task[None] | None = None
    _readiness: Readiness | None = None
    _set_up = False
    _ready = False
    _tasks_on = False

    def __init__(
        self,
        *,
        intents: Intents = Intents.DEFAULT,
        dev_guilds: Iterable[int] = (),
        auto_sync: bool = True,
        contexts: Iterable[InteractionContextType] | None = None,
        integration_types: Iterable[ApplicationIntegrationType] | None = None,
        translate: Translate | None = None,
        auto_defer: float | None = 2.5,
        cache: bool = True,
        max_messages: int = 0,
        members: bool = False,
        chunk_members: bool = True,
        presences: bool = False,
        max_dm_channels: int = 0,
        guild_ready_timeout: float = 2.0,
        large_threshold: int = 50,
        allowed_mentions: AllowedMentions = DEFAULT_ALLOWED_MENTIONS,
        status: Status = Status.ONLINE,
        activities: Iterable[ActivityDict] = (),
        afk: bool = False,
        idle_since: datetime | None = None,
        http_settings: HTTPSettings = DEFAULT_HTTP,
        api_url: str | None = None,
        voice: Defaults = DEFAULT_VOICE,
        shard_count: int | Literal["auto"] = "auto",
        shard_ids: Iterable[int] | Literal["all"] = "all",
        before_identify: Callable[[int], Awaitable[object]] | None = None,
        session_file: str | os.PathLike[str] | None = None,
        shutdown_grace: float = 0.0,
        owner_ids: Iterable[int] | None = None,
    ) -> None:
        super().__init__()
        _check_cache_options(
            intents,
            cache=cache,
            max_messages=max_messages,
            members=members,
            presences=presences,
            max_dm_channels=max_dm_channels,
        )
        if large_threshold not in _LARGE_THRESHOLD:
            raise ValueError(
                f"large_threshold must be 50 to 250, got {large_threshold}"
            )
        self._shard_count = shard_count
        self._shard_ids = _shard_ids(shard_count, shard_ids)
        self.cache = Cache(
            max_messages,
            members=members,
            presences=presences,
            max_dm_channels=max_dm_channels,
        )
        self.voice = VoiceManager(
            self, voice, lambda: None if self._bridge is None else self._bridge.core
        )
        self.lookup = Lookup(self)
        self._intents = intents
        self._dev_guilds = tuple(dev_guilds)
        self._command_ids: dict[int | None, Mapping[str, int]] = {}
        self._command_defaults = _command_defaults(contexts, integration_types)
        self._translate = translate
        self._auto_sync = auto_sync
        self.auto_defer = auto_defer
        self._caching = cache
        self._guild_ready_timeout = guild_ready_timeout
        self._chunk_members = members and chunk_members
        self._presences_cached = presences
        self._large_threshold = large_threshold
        self._before_identify = before_identify
        self._session_file = None if session_file is None else Path(session_file)
        self._shutdown_grace = shutdown_grace
        self._given_owners = owner_ids is not None
        self._owner_ids = frozenset(owner_ids or ())
        self._allowed_mentions = allowed_mentions
        self._presence = presence_fields(
            status=status, activities=activities, afk=afk, idle_since=idle_since
        )
        self._http_settings = http_settings
        self._api_url = api_url
        self._unready: set[int] = set()
        self._ready_waiters: list[asyncio.Future[None]] = []
        self._waiters: dict[str, list[_Waiter[Any]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._deletions: set[asyncio.Task[None]] = set()
        self._running_tasks: dict[Task, asyncio.Task[None]] = {}
        self._replaying: set[int] = set()
        self._failed_dispatches: set[str] = set()
        self._requests: dict[tuple[str, object], list[_Request]] = {}
        self._interactions = Interactions(
            self,
            tasks=self._tasks,
            replaying=self._replaying,
            allowed_mentions=allowed_mentions,
            deletions=self._deletions,
        )

    @property
    @override
    def bot(self) -> "Bot":
        """This bot, so code written against a Router works on the bot too."""
        return self

    @property
    def http(self) -> HTTPClient:
        """The REST client, for any endpoint; it exists once `start()` runs."""
        if self._http is None:
            raise StateError("bot.http exists once the bot has started")
        return self._http

    @property
    def rest(self) -> REST:
        """Discord's REST operations by resource; they exist once `start()` runs."""
        if self._rest is None:
            raise StateError("bot.rest exists once the bot has started")
        return self._rest

    @property
    def gateway(self) -> GatewayClient:
        """The gateway connection, for any opcode; it exists once `start()` runs."""
        return self._running_shards()

    def _running_shards(self) -> Shards:
        if self._shards is None:
            raise StateError("bot.gateway exists once the bot has started")
        return self._shards

    @property
    def latency(self) -> float | None:
        """Mean seconds from a heartbeat to its ACK over the shards, or None."""
        return None if self._shards is None else self._shards.latency

    @property
    def intents(self) -> Intents:
        """The gateway intents the bot was created with."""
        return self._intents

    @property
    def owner_ids(self) -> frozenset[int]:
        """Who `checks.is_owner` lets through: the given ids, else the app's owners."""
        return self._owner_ids

    @property
    def is_running(self) -> bool:
        """Whether `start()` or `open()` runs the bot, until `close()` ends it."""
        return self._bridge is not None

    @property
    def is_ready(self) -> bool:
        """Whether Started was sent since the last start(): every shard was ready."""
        return self._ready

    def run(
        self,
        token: str | None = None,
        *,
        loop_factory: Callable[[], asyncio.AbstractEventLoop] | None = None,
    ) -> None:
        """Run the bot until it stops; Ctrl+C and SIGTERM close it cleanly.

        Logging goes to stderr at INFO unless the root logger is already configured.
        """
        logging.basicConfig(level=logging.INFO)
        asyncio.run(self._run(token), loop_factory=loop_factory)

    async def start(self, token: str | None = None) -> None:
        """Connect, sync commands and handle events until `close()` or a fatal error.

        The token defaults to the BOSTIE_TOKEN environment variable.
        """
        bridge, token = self._open(token)
        async with bridge:
            try:
                await self._connect(bridge, token)
            except (TransportError, StateError):
                if self._closing is None:
                    raise
            finally:
                await self.close()

    async def open(self, token: str | None = None) -> Application:
        """Start REST, send Starting, sync commands and run tasks, with no gateway."""
        bridge, _ = self._open(token)
        await bridge.__aenter__()
        try:
            application = await self._prepare(fetch_user=True)
        except BaseException:
            await self.close()
            raise
        self._start_tasks()
        return application

    def _open(self, token: str | None) -> tuple[Bridge, str]:
        if self._bridge is not None:
            raise StateError("the bot is already running")
        try:
            token = resolve_token(token)
            bridge = Bridge(
                token,
                self._api_url,
                self._on_gateway,
                http=self._http_settings,
            )
        except Exception:
            self._stop_ready_waiters()
            raise
        if self._closing is not None and self._closing.done():
            self._closing = None
        self._bridge = bridge
        self._replaying.clear()
        self._command_ids.clear()
        self._http = HTTPClient(bridge)
        self._rest = REST(
            self._http, self._allowed_mentions, self.cache, self._deletions
        )
        self._shards = None
        return bridge, token

    async def _prepare(self, *, fetch_user: bool) -> Application:
        application = await self.rest.application.fetch()
        self._adopt(application)
        if fetch_user:
            self.user = await self.rest.users.fetch_current()
            self.cache._set_current_user(self.user)
        await self._setup()
        if self._auto_sync:
            await self._sync(always_put=False)
        return application

    async def _connect(self, bridge: Bridge, token: str) -> None:
        http = self.http
        info = await http.request("GET", "/gateway/bot")
        count = (
            self._shard_count
            if isinstance(self._shard_count, int)
            else int(info["shards"])
        )
        self._shards = shards = Shards(
            bridge,
            token,
            intents=self._intents,
            shard_count=count,
            shard_ids=range(count)
            if isinstance(self._shard_ids, str)
            else self._shard_ids,
            large_threshold=self._large_threshold,
            session_file=self._session_file,
        )
        self._unready = set(shards.shard_ids)
        self._readiness = Readiness(
            shards.shard_ids,
            self._shard_ready,
            guilds=bool(self._intents & Intents.GUILDS),
            timeout=self._guild_ready_timeout,
            tasks=self._tasks,
            chunk=partial(self.request_members, presences=self._presences_cached)
            if self._chunk_members
            else None,
            small_guilds_complete=bool(self._intents & Intents.GUILD_PRESENCES),
        )
        await self._prepare(fetch_user=False)
        if resumed := shards.load_sessions():
            self._replaying |= resumed
            self.user = await self.rest.users.fetch_current()
            self.cache._set_current_user(self.user)
        shards.filter(self._dispatch_names())
        shards.identify_limit(info["session_start_limit"])
        if self._before_identify is not None:
            shards.gate_identifies()
        await shards.start(info["url"], self._presence)

    async def close(self) -> None:
        """Disconnect, cancel running handlers after `shutdown_grace`, close the bridge.

        The shards end their sessions with code 1000, or save them to the session file.
        """
        if self._closing is None or self._closing.done():
            self._closing = asyncio.create_task(self._shut_down())
        await asyncio.shield(self._closing)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def wait_until_ready(self) -> None:
        """Wait until every shard is ready; raise StateError if the bot stops first."""
        if self._ready:
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._ready_waiters.append(future)
        try:
            await future
        finally:
            self._ready_waiters.remove(future)

    async def wait_for[E: AnyEvent](
        self,
        event: type[E],
        *,
        check: Callable[[E], bool] | None = None,
    ) -> E:
        """Wait for the next `event` that passes `check`.

        An exception from `check` is raised here; bound the wait with asyncio.timeout().
        """
        async with self._waiting(event, check, once=True) as events:
            return await anext(events)

    @contextlib.asynccontextmanager
    async def stream[E: AnyEvent](
        self,
        event: type[E],
        *,
        check: Callable[[E], bool] | None = None,
    ) -> AsyncGenerator[AsyncIterator[E]]:
        """Queue each `event` passing `check` for `async for` while the block runs."""
        async with self._waiting(event, check, once=False) as events:
            yield events

    async def wait_for_component(
        self,
        *custom_ids: str,
        check: Callable[[ComponentContext], bool] | None = None,
        ephemeral: bool = False,
        auto_defer: float | Unset | None = UNSET,
    ) -> ComponentContext:
        """Wait for a use of a component with one of `custom_ids`, or of any.

        The context answers like a route's, and no route runs for that use. An
        exception from `check` is raised here; bound the wait with asyncio.timeout().
        """
        return await self._claim(
            ComponentContext,
            custom_ids,
            check,
            ephemeral=ephemeral,
            auto_defer=auto_defer,
        )

    async def wait_for_modal(
        self,
        *custom_ids: str,
        check: Callable[[ModalContext], bool] | None = None,
        ephemeral: bool = False,
        auto_defer: float | Unset | None = UNSET,
    ) -> ModalContext:
        """Wait for the submit of a modal with one of `custom_ids`, or of any.

        The context answers like a route's, and no route runs for that submit.
        """
        return await self._claim(
            ModalContext, custom_ids, check, ephemeral=ephemeral, auto_defer=auto_defer
        )

    async def _claim[C: ComponentContext | ModalContext](
        self,
        context: type[C],
        custom_ids: Iterable[str],
        check: Callable[[C], bool] | None,
        *,
        ephemeral: bool,
        auto_defer: float | Unset | None,
    ) -> C:
        future: asyncio.Future[C] = asyncio.get_running_loop().create_future()
        claim = Claim(
            context, frozenset(custom_ids), check, ephemeral, auto_defer, future
        )
        self._interactions.claims.append(claim)
        self._refresh_filter()
        try:
            return await future
        finally:
            self._interactions.claims.remove(claim)
            self._refresh_filter()

    @contextlib.asynccontextmanager
    async def _waiting[E: AnyEvent](
        self, event: type[E], check: Callable[[E], bool] | None, *, once: bool
    ) -> AsyncGenerator[AsyncIterator[E]]:
        waiter = _Waiter(event, check, asyncio.Queue[E | Exception](), once)
        waiters = self._waiters.setdefault(event.NAME, [])
        waiters.append(waiter)
        if len(waiters) == 1:
            self._refresh_filter()
        try:
            yield _queued(waiter.queue)
        finally:
            waiters.remove(waiter)
            if not waiters:
                del self._waiters[event.NAME]
                self._refresh_filter()

    def dispatch(self, event: _LocalEvent) -> None:
        """Send an event of your own to its listeners, waiters and streams."""
        self._emit(event)

    def _deliver(self, name: str, data: bytes) -> None:
        """Handle an event payload that came over HTTP as if the gateway sent it."""
        self._dispatch(0, 0, name, data, asyncio.get_running_loop().time())

    async def update_presence(
        self,
        *,
        status: Status | None = None,
        activities: Iterable[ActivityDict] | None = None,
        afk: bool | None = None,
        idle_since: datetime | Unset | None = UNSET,
    ) -> None:
        """Change what the bot shows on every shard, and on sessions it starts later."""
        self._presence |= presence_fields(
            status=status, activities=activities, afk=afk, idle_since=idle_since
        )
        if self._shards is not None:
            self._shards.update_presence(self._presence)

    async def request_members(
        self,
        guild_id: int,
        *,
        query: str | None = None,
        limit: int | None = None,
        user_ids: Iterable[int] | None = None,
        presences: bool = False,
        timeout: float = REQUEST_TIMEOUT,
    ) -> RequestedMembers:
        """Ask the gateway for members of a guild and gather every chunk of the answer.

        Without `query` or `user_ids` it asks for every member. `timeout` bounds the
        wait for each chunk, 30 s by default, and raises TimeoutError when it ends.
        """
        request = member_request(
            guild_id,
            self._intents,
            query=query,
            limit=limit,
            user_ids=user_ids,
            presences=presences,
        )
        chunks: list[MembersChunk] = []
        key = (MembersChunk.NAME, request["nonce"])
        async with self._request(
            guild_id, REQUEST_GUILD_MEMBERS, request, key, timeout
        ) as answers:
            async for answer in answers:
                if isinstance(answer, GatewayRateLimited):
                    raise MemberRequestRateLimited(answer.retry_after)
                chunks.append(cast("MembersChunk", answer))
                if len(chunks) >= chunks[-1].chunk_count:
                    break
        return RequestedMembers(
            members=tuple(member for chunk in chunks for member in chunk.members),
            presences=tuple(
                presence for chunk in chunks for presence in chunk.presences
            ),
            not_found=tuple(user_id for chunk in chunks for user_id in chunk.not_found),
        )

    async def request_soundboard_sounds(
        self, guild_id: int
    ) -> tuple[SoundboardSound, ...]:
        """Ask the gateway for a guild's soundboard sounds; TimeoutError after 30 s."""
        request = {"guild_ids": [str(guild_id)]}
        key = (SoundboardSounds.NAME, guild_id)
        async with self._request(
            guild_id, REQUEST_SOUNDBOARD_SOUNDS, request, key, REQUEST_TIMEOUT
        ) as answers:
            answer = await anext(answers)
        return cast("SoundboardSounds", answer).soundboard_sounds

    async def request_channel_info(
        self,
        guild_id: int,
        *,
        fields: Iterable[Literal["status", "voice_start_time"]] = _CHANNEL_INFO_FIELDS,
    ) -> tuple[VoiceChannelInfo, ...]:
        """Ask the gateway for the voice status and start time of a guild's channels."""
        request = {"guild_id": str(guild_id), "fields": list(fields)}
        key = (ChannelInfo.NAME, guild_id)
        async with self._request(
            guild_id, REQUEST_CHANNEL_INFO, request, key, REQUEST_TIMEOUT
        ) as answers:
            answer = await anext(answers)
        return cast("ChannelInfo", answer).channels

    @contextlib.asynccontextmanager
    async def _request(
        self,
        guild_id: int,
        op: int,
        d: Mapping[str, Any],
        key: tuple[str, object],
        wait: float,
    ) -> AsyncGenerator[AsyncIterator[Event]]:
        gateway = self.gateway
        shard_id = gateway.shard_for(guild_id)
        if self._readiness is not None and self._readiness.renewing(shard_id):
            raise StateError(
                f"shard {shard_id} is starting a new session; retry once it is ready"
            )
        request = _Request(shard_id)
        pending = self._requests.setdefault(key, [])
        pending.append(request)
        self._refresh_filter()
        try:
            gateway.send(op, d)
            yield _queued(request.answers, wait)
        finally:
            pending.remove(request)
            if not pending:
                del self._requests[key]
            self._refresh_filter()

    async def sync_commands(self) -> list[SyncResult]:
        """Overwrite the commands on Discord now and return what differed."""
        return await self._sync(always_put=True)

    def _adopt(self, application: Application) -> None:
        self.application_id = application.id
        if not self._given_owners:
            self._owner_ids = _owners(application)
        self._install_types = tuple(sorted(application.integration_types_config)) or (
            ApplicationIntegrationType.GUILD_INSTALL,
        )

    async def _sync(self, *, always_put: bool) -> list[SyncResult]:
        if self.application_id is None:
            raise StateError("sync_commands() needs a running bot")
        results = await sync_commands(
            self.http,
            self.application_id,
            [*self.commands.values(), *self.context_menus.values()],
            self._dev_guilds,
            install_types=self._install_types,
            defaults=self._command_defaults,
            translate=self._translate,
            always_put=always_put,
        )
        self._remember_ids(results)
        return results

    async def command_mention(self, name: str, *, guild_id: int | None = None) -> str:
        """`</name:id>`, shown as a clickable `/name`; `name` may end in subcommands.

        In `guild_id`, a command registered there wins over a global one. Without
        a sync since the start, it asks Discord for the ids once per scope.
        """
        if self.application_id is None:
            raise StateError("command_mention() needs a running bot")
        top = name.split(" ", 1)[0]
        for scope in (None,) if guild_id is None else (guild_id, None):
            ids = self._command_ids.get(scope)
            if ids is None:
                try:
                    ids = await fetch_ids(self.http, self.application_id, scope)
                except Forbidden:
                    if scope is None:
                        raise
                    ids = {}
                self._command_ids[scope] = ids
            if top in ids:
                return fmt.command(name, ids[top])
        raise LookupError(f"no slash command named {top!r} is registered there")

    def _remember_ids(self, results: Iterable[SyncResult]) -> None:
        for result in results:
            self._command_ids[result.guild_id] = result.ids

    async def _run(self, token: str | None) -> None:
        started = asyncio.create_task(self.start(token))
        if threading.current_thread() is threading.main_thread():
            with contextlib.suppress(NotImplementedError):
                asyncio.get_running_loop().add_signal_handler(
                    signal.SIGTERM, started.cancel
                )
        with contextlib.suppress(asyncio.CancelledError):
            await started

    async def _shut_down(self) -> None:
        self._tasks_on = False
        if self._ready:
            running = set(self._tasks)
            self._emit(Stopping())
            if stopping := self._tasks - running:
                await asyncio.wait(stopping, timeout=_TASK_CANCEL_TIMEOUT)
        await self.voice.close()
        if self._shards is not None:
            await self._shards.close()
        if self._readiness is not None:
            self._readiness.close()
        loops = set(self._running_tasks.values())
        for task in [*loops, *self._deletions]:
            task.cancel()
        busy = self._tasks - loops
        if self._shutdown_grace and busy:
            await asyncio.wait(busy, timeout=self._shutdown_grace)
        for task in self._tasks - loops:
            task.cancel()
        if pending := list(self._tasks):
            await asyncio.wait(pending, timeout=_TASK_CANCEL_TIMEOUT)
        self._running_tasks.clear()
        if self._bridge is not None:
            await self._bridge.close()
        self._bridge = None
        self._ready = False
        self._stop_ready_waiters()
        if self._set_up:
            await self._teardown()

    async def _teardown(self) -> None:
        self._set_up = False
        await self._emit_in_turn(Stopped(), raising=False)

    async def _setup(self) -> None:
        self._set_up = True
        await self._emit_in_turn(Starting(), raising=True)

    async def _emit_in_turn(self, event: Starting | Stopped, *, raising: bool) -> None:
        for waiter in self._waiters.get(event.NAME, ()):
            _resolve(waiter, event)
        for listener in list(self.listeners.get(event.NAME, ())):
            if raising:
                await listener.callback(event)
            else:
                await self._run_listener(listener, event, event.NAME)

    def _stop_ready_waiters(self) -> None:
        for future in self._ready_waiters:
            if not future.done():
                future.set_exception(StateError("the bot stopped before it was ready"))

    def _dispatch_names(self) -> list[str] | None:
        names = {
            name
            for name in (*self.listeners, *self._waiters, GatewayRateLimited.NAME)
            if "." not in name
        }
        if _RAW_EVENT_NAME in names:
            return None
        names |= {name for name, _ in self._requests}
        if self._intents & Intents.GUILDS:
            names |= {GuildCreate.NAME, GuildDelete.NAME}
        if self._caching:
            names |= self.cache.names
        if (
            self.commands
            or self.context_menus
            or self.component_routes
            or self.modal_routes
            or self._interactions.claims
        ):
            names.add(InteractionCreate.NAME)
        return sorted(names)

    def _refresh_filter(self) -> None:
        if self._shards is not None:
            self._shards.filter(self._dispatch_names())

    @override
    def _registered(self, listener: Listener | None = None) -> None:
        self._refresh_filter()
        self._run_tasks()
        event = None if listener is None else listener.event
        if listener is None or event is None or not event.INTENTS:
            return
        if not event.INTENTS & self._intents:
            privileged = event.INTENTS & Intents.PRIVILEGED == event.INTENTS
            _log.warning(
                "%s listens to %s, which needs the %s intent; add it to "
                "Bot(intents=...)%s",
                qualified_name(listener.callback),
                event.NAME,
                event.INTENTS.name,
                " and turn it on in the Developer Portal" if privileged else "",
            )

    def _on_gateway(self, delivery: GatewayDelivery, received: float) -> None:
        match delivery:
            case ("dispatch", shard_id, seq, name, data):
                try:
                    self._dispatch(shard_id, seq, name, data, received)
                except Exception:
                    _log.log(
                        logging.DEBUG
                        if name in self._failed_dispatches
                        else logging.ERROR,
                        "handling %s failed",
                        name,
                        exc_info=True,
                    )
                    self._failed_dispatches.add(name)
            case ("disconnected", shard_id, code, reason, resuming):
                if resuming:
                    self._replaying.add(shard_id)
                else:
                    self._replaying.discard(shard_id)
                    self._end_session(shard_id)
                self._running_shards().handle(delivery)
                self._emit(
                    ShardDisconnect(
                        shard_id=shard_id, code=code, reason=reason, resuming=resuming
                    )
                )
            case ("identify", shard_id, request):
                spawn(self._tasks, self._approve_identify(shard_id, request))
            case ("stopped", shard_id, code, reason, _):
                self._running_shards().handle(delivery)
                self._emit(ShardStop(shard_id=shard_id, code=code, reason=reason))
            case ("backpressure", shard_id, queued_bytes):
                self._running_shards().handle(delivery)
                self._emit(
                    ShardBackpressure(shard_id=shard_id, queued_bytes=queued_bytes)
                )
            case ("invalid_requests", count):
                warn_invalid_requests(count)
            case _:
                self.voice.handle(delivery)

    async def _approve_identify(self, shard_id: int, request: int) -> None:
        if self._before_identify is not None:
            try:
                await self._before_identify(shard_id)
            except Exception:
                _log.exception(
                    "before_identify raised for shard %d; it identifies anyway",
                    shard_id,
                )
        self._running_shards().approve_identify(shard_id, request)

    def _end_session(self, shard_id: int) -> None:
        if self._readiness is None or not self._readiness.ended(shard_id):
            return
        dropped = StateError(
            f"shard {shard_id} started a new session, which dropped the request"
        )
        for pending in self._requests.values():
            for request in pending:
                if request.shard_id == shard_id:
                    request.answers.put_nowait(dropped)

    def _emit(self, event: _LocalEvent) -> None:
        for waiter in self._waiters.get(event.NAME, ()):
            _resolve(waiter, event)
        for listener in self.listeners.get(event.NAME, ()):
            spawn(
                self._tasks,
                self._run_listener(listener, event, event.NAME),
                eager=True,
            )

    async def _run_listener(
        self, listener: Listener, event: AnyEvent, name: str
    ) -> None:
        try:
            await listener.callback(event)
        except Exception as error:
            if isinstance(event, ListenerError):
                _log.exception(
                    "the ListenerError listener %s raised",
                    qualified_name(listener.callback),
                )
                return
            if ListenerError.NAME not in self.listeners:
                _log.exception(
                    "the listener %s raised on %s",
                    qualified_name(listener.callback),
                    name,
                )
            self._emit(
                ListenerError(listener=listener.callback, event=event, error=error)
            )

    def _start_tasks(self) -> None:
        self._tasks_on = True
        self._run_tasks()

    def _run_tasks(self) -> None:
        if not self._tasks_on:
            return
        wanted = {task for task in self.tasks.values() if not task.stopped}
        for task in self._running_tasks.keys() - wanted:
            self._running_tasks.pop(task).cancel()
        for task in wanted - self._running_tasks.keys():
            running = asyncio.create_task(task._run(self._task_failed))
            running.add_done_callback(self._tasks.discard)
            self._tasks.add(running)
            self._running_tasks[task] = running

    def _task_failed(self, task: Task, error: Exception) -> None:
        if TaskError.NAME not in self.listeners:
            _log.error(
                "the task %s raised", qualified_name(task.callback), exc_info=error
            )
        self._emit(TaskError(task=task, error=error))

    def _shard_ready(self, shard_id: int) -> None:
        self._emit(ShardReady(shard_id=shard_id))
        self._unready.discard(shard_id)
        if self._unready or self._ready:
            return
        self._ready = True
        for future in self._ready_waiters:
            if not future.done():
                future.set_result(None)
        self._start_tasks()
        self._emit(Started())

    def _dispatch(
        self, shard_id: int, seq: int, name: str, data: bytes, received: float
    ) -> None:
        dispatch = _Dispatch(shard_id, seq, name, data)
        readied = self._follow_session(dispatch)
        self._answer_request(dispatch)
        if self._caching and name in self.cache.names:
            cls = EVENTS[name]
            cached = dispatch.event(cls)
            if cached is not None:
                dispatch.decoded[cls] = self.cache._apply(cached)
        for key in (name, _RAW_EVENT_NAME):
            for waiter in self._waiters.get(key, ()):
                _resolve(waiter, dispatch.event(waiter.event))
        if name == InteractionCreate.NAME:
            created = dispatch.event(InteractionCreate)
            if created is not None:
                self._interactions.route(created.interaction, received, shard_id)
        for listener in (
            *self.listeners.get(name, ()),
            *self.listeners.get(_RAW_EVENT_NAME, ()),
        ):
            payload = dispatch.event(
                RawEvent
                if listener.event is None
                else cast("type[Event]", listener.event)
            )
            if payload is not None:
                spawn(
                    self._tasks,
                    self._run_listener(listener, payload, name),
                    eager=True,
                )
        if readied:
            self._shard_ready(shard_id)

    def _follow_session(self, dispatch: _Dispatch) -> bool:
        readiness = self._readiness
        match dispatch.name:
            case Ready.NAME:
                self._replaying.discard(dispatch.shard_id)
                ready = dispatch.event(Ready)
                if ready is not None:
                    self.user = ready.user
                    self.application_id = ready.application.id
                if readiness is not None:
                    return readiness.ready(dispatch.shard_id, dispatch.data)
            case Resumed.NAME:
                self._replaying.discard(dispatch.shard_id)
                if readiness is not None:
                    return readiness.resumed(dispatch.shard_id)
            case UserUpdate.NAME:
                update = dispatch.event(UserUpdate)
                if update is not None:
                    self.user = update.user
            case GuildCreate.NAME if readiness is not None:
                created = dispatch.event(GuildCreate)
                joined, readied = readiness.created(
                    dispatch.shard_id,
                    dispatch.data,
                    None if created is None else created.guild,
                )
                if created is not None:
                    dispatch.decoded[GuildCreate] = msgspec.structs.replace(
                        created, joined=joined
                    )
                return readied
            case GuildDelete.NAME if readiness is not None:
                return readiness.deleted(dispatch.shard_id, dispatch.data)
            case _:
                pass
        return False

    def _answer_request(self, dispatch: _Dispatch) -> None:
        match dispatch.name:
            case MembersChunk.NAME:
                chunk = dispatch.event(MembersChunk)
                if chunk is not None:
                    self._answer((MembersChunk.NAME, chunk.nonce), chunk)
            case GatewayRateLimited.NAME:
                limited = dispatch.event(GatewayRateLimited)
                if limited is None:
                    return
                nonce = None if limited.meta is None else limited.meta.nonce
                if not self._answer((MembersChunk.NAME, nonce), limited):
                    _log.warning(
                        "the gateway rate limited opcode %s; retry after %s s",
                        limited.opcode,
                        limited.retry_after,
                    )
            case SoundboardSounds.NAME:
                sounds = dispatch.event(SoundboardSounds)
                if sounds is not None:
                    self._answer((SoundboardSounds.NAME, sounds.guild_id), sounds)
            case ChannelInfo.NAME:
                info = dispatch.event(ChannelInfo)
                if info is not None:
                    self._answer((ChannelInfo.NAME, info.guild_id), info)
            case _:
                pass

    def _answer(self, key: tuple[str, object], answer: Event) -> bool:
        pending = self._requests.get(key, ())
        for request in pending:
            request.answers.put_nowait(answer)
        return bool(pending)


def _resolve[E: AnyEvent](waiter: _Waiter[E], event: E | None) -> None:
    if event is None or waiter.closed:
        return
    try:
        matched = waiter.check is None or waiter.check(event)
    except Exception as error:
        waiter.queue.put_nowait(error)
        waiter.closed = True
        return
    if matched:
        waiter.queue.put_nowait(event)
        waiter.closed = waiter.once


async def _queued[T](
    queue: asyncio.Queue[T | Exception], wait: float | None = None
) -> AsyncIterator[T]:
    while True:
        async with asyncio.timeout(wait):
            item = await queue.get()
        if isinstance(item, Exception):
            raise item
        yield item


def _owners(application: Application) -> frozenset[int]:
    team = application.team
    if team is None:
        return frozenset(() if application.owner is None else (application.owner.id,))
    return frozenset(
        {
            team.owner_user_id,
            *(
                member.user.id
                for member in team.members
                if member.membership_state is MembershipState.ACCEPTED
                and member.role in _OWNER_ROLES
            ),
        }
    )


def _command_defaults(
    contexts: Iterable[InteractionContextType] | None,
    integration_types: Iterable[ApplicationIntegrationType] | None,
) -> _CommandDefaults:
    defaults = _CommandDefaults()
    if contexts is not None:
        defaults["contexts"] = tuple(contexts)
    if integration_types is not None:
        defaults["integration_types"] = tuple(integration_types)
    return defaults


def _check_cache_options(
    intents: Intents,
    *,
    cache: bool,
    max_messages: int,
    members: bool,
    presences: bool,
    max_dm_channels: int,
) -> None:
    for name, value in (
        ("max_messages", max_messages),
        ("members", members),
        ("presences", presences),
        ("max_dm_channels", max_dm_channels),
    ):
        if value and not cache:
            raise ValueError(f"{name} needs cache=True")
    if members and not intents & Intents.GUILD_MEMBERS:
        raise MissingIntent("members=True needs Intents.GUILD_MEMBERS")
    if presences and not intents & Intents.GUILD_PRESENCES:
        raise MissingIntent("presences=True needs Intents.GUILD_PRESENCES")


def _shard_ids(
    count: int | Literal["auto"], ids: Iterable[int] | Literal["all"]
) -> tuple[int, ...] | Literal["all"]:
    if isinstance(count, int) and count < 1:
        raise ValueError(f"shard_count must be at least 1, got {count}")
    if isinstance(ids, str):
        return ids
    if count == "auto":
        raise ValueError("shard_ids needs an explicit shard_count")
    chosen = tuple(ids)
    if not chosen or len(set(chosen)) != len(chosen):
        raise ValueError(f"shard_ids must be distinct and not empty, got {chosen}")
    if outside := [shard_id for shard_id in chosen if not 0 <= shard_id < count]:
        raise ValueError(f"shard_ids {outside} are outside range({count})")
    return chosen
