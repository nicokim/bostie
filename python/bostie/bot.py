import asyncio
import contextlib
import logging
import os
import signal
import threading
import time
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
)
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal, Unpack, cast, override

import msgspec

from bostie import sync
from bostie._aio import spawn
from bostie._bridge import Bridge, GatewayDelivery
from bostie.activity import ActivityDict
from bostie.cache import Cache
from bostie.commands import (
    AutocompleteCallback,
    Command,
    ContextMenuCommand,
    Group,
    _python_name,
    autocomplete_choices,
)
from bostie.context import (
    AutocompleteContext,
    ComponentContext,
    Context,
    ModalContext,
    ResponseState,
    _split_subcommands,
)
from bostie.custom_id import ComponentRoute
from bostie.enums import CommandType, Intents, Status
from bostie.errors import (
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
from bostie.gateway import (
    LARGE_THRESHOLD,
    MEMBER_REQUEST,
    PRESENCE_OPTIONS,
    REQUEST_CHANNEL_INFO,
    REQUEST_GUILD_MEMBERS,
    REQUEST_SOUNDBOARD_SOUNDS,
    REQUEST_TIMEOUT,
    GatewayClient,
    MemberRequest,
    PresenceOptions,
    Readiness,
    RequestedMembers,
    member_request,
    presence_fields,
)
from bostie.http import DEFAULT_HTTP, HTTPClient, HTTPSettings
from bostie.models import (
    AllowedMentions,
    AutocompleteInteraction,
    CommandData,
    CommandInteraction,
    ComponentInteraction,
    CurrentUser,
    Interaction,
    InteractionDataOption,
    ModalSubmitInteraction,
    SoundboardSound,
    UnknownInteraction,
    VoiceChannelInfo,
)
from bostie.models.base import check_keys
from bostie.rest import REST
from bostie.router import RAW_EVENT_NAME, AnyEvent, Listener, Router
from bostie.tasks import TaskCallback
from bostie.voice.connection import VoiceManager
from bostie.voice.events import VoiceEvent
from bostie.voice.formats import Defaults

log = logging.getLogger("bostie.bot")

TOKEN_VARIABLE = "BOSTIE_TOKEN"
DISCORD_EPOCH_MS = 1_420_070_400_000
STALE_INTERACTION_MS = 5000
SLOW_HANDLER_START = 1.0
TASK_CANCEL_TIMEOUT = 5.0
FAILURE_ANSWER = "Something went wrong."
PRIVILEGED_INTENTS = (
    Intents.GUILD_MEMBERS | Intents.GUILD_PRESENCES | Intents.MESSAGE_CONTENT
)
CHANNEL_INFO_FIELDS: tuple[Literal["status", "voice_start_time"], ...] = (
    "status",
    "voice_start_time",
)
DEFAULT_ALLOWED_MENTIONS = AllowedMentions(parse=("users", "roles"))
DEFAULT_VOICE = Defaults()

type Handler = Callable[..., Coroutine[Any, Any, None]]
type LocalEvent = VoiceEvent | BotEvent


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
    fields: dict[str, Any] = field(default_factory=dict[str, Any])
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
                event = decode_event(cls, self.shard_id, self.data)
                if event is not None and self.fields:
                    event = msgspec.structs.replace(event, **self.fields)
                self.decoded[cls] = event
        return cast("E | None", self.decoded[cls])


class Bot(Router):
    """A Discord bot: commands, components, listeners, REST and its gateway shards."""

    def __init__(
        self,
        *,
        intents: Intents = Intents.DEFAULT,
        dev_guilds: Iterable[int] = (),
        auto_sync: bool = True,
        auto_defer: float | None = 2.5,
        cache: bool = True,
        max_messages: int = 0,
        members: bool = False,
        chunk_members: bool = True,
        presences: bool = False,
        max_dm_channels: int = 0,
        guild_ready_timeout: float = 2.0,
        large_threshold: int = LARGE_THRESHOLD.start,
        allowed_mentions: AllowedMentions = DEFAULT_ALLOWED_MENTIONS,
        status: Status = Status.ONLINE,
        activities: Iterable[ActivityDict] = (),
        afk: bool = False,
        idle_since: datetime | None = None,
        http_settings: HTTPSettings = DEFAULT_HTTP,
        api_url: str = "https://discord.com/api/v10",
        voice: Defaults = DEFAULT_VOICE,
        shard_count: int | Literal["auto"] = "auto",
        shard_ids: Iterable[int] | Literal["all"] = "all",
        before_identify: Callable[[int], Awaitable[object]] | None = None,
        session_file: str | os.PathLike[str] | None = None,
    ) -> None:
        super().__init__()
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
        if large_threshold not in LARGE_THRESHOLD:
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
        self.voice = VoiceManager(self, voice)
        self.user: CurrentUser | None = None
        self.application_id: int | None = None
        self._intents = intents
        self._dev_guilds = tuple(dev_guilds)
        self._auto_sync = auto_sync
        self.auto_defer = auto_defer
        self._caching = cache
        self._guild_ready_timeout = guild_ready_timeout
        self._chunk_members = members and chunk_members
        self._presences_cached = presences
        self._large_threshold = large_threshold
        self._before_identify = before_identify
        self._session_file = None if session_file is None else Path(session_file)
        self._allowed_mentions = allowed_mentions
        self._presence = presence_fields(
            {
                "status": status,
                "activities": activities,
                "afk": afk,
                "idle_since": idle_since,
            }
        )
        self._http_settings = http_settings
        self._api_url = api_url
        self._bridge: Bridge | None = None
        self._http: HTTPClient | None = None
        self._rest: REST | None = None
        self._gateway: GatewayClient | None = None
        self._closing: asyncio.Task[None] | None = None
        self._set_up = False
        self._ready = False
        self._unready: set[int] = set()
        self._readiness: Readiness | None = None
        self._ready_waiters: list[asyncio.Future[None]] = []
        self._waiters: dict[str, list[_Waiter[Any]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._running_tasks: dict[TaskCallback, asyncio.Task[None]] = {}
        self._tasks_on = False
        self._replaying: set[int] = set()
        self._failed_dispatches: set[str] = set()
        self._requests: dict[tuple[str, object], list[_Request]] = {}

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
        if self._gateway is None:
            raise StateError("bot.gateway exists once the bot has started")
        return self._gateway

    @property
    def latency(self) -> float | None:
        """Mean seconds from a heartbeat to its ACK over the shards, or None."""
        return None if self._gateway is None else self._gateway.latency

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
        if self._bridge is not None:
            raise StateError("the bot is already running")
        try:
            token = _resolve_token(token)
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
        async with bridge:
            self._http = http = HTTPClient(bridge)
            self._rest = REST(http, self._allowed_mentions, self.cache)
            self._gateway = None
            try:
                info = await http.request("GET", "/gateway/bot")
                count = (
                    self._shard_count
                    if isinstance(self._shard_count, int)
                    else int(info["shards"])
                )
                self._gateway = gateway = GatewayClient(
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
                self._unready = set(gateway.shard_ids)
                self._readiness = Readiness(
                    gateway.shard_ids,
                    self._shard_ready,
                    guilds=bool(self._intents & Intents.GUILDS),
                    timeout=self._guild_ready_timeout,
                    tasks=self._tasks,
                    chunk=partial(
                        self.request_members, presences=self._presences_cached
                    )
                    if self._chunk_members
                    else None,
                    small_guilds_complete=bool(self._intents & Intents.GUILD_PRESENCES),
                )
                application = await http.request("GET", "/applications/@me")
                self.application_id = int(application["id"])
                await self._setup()
                if self._auto_sync:
                    await sync.sync_commands(
                        http,
                        self.application_id,
                        [*self.commands.values(), *self.context_menus.values()],
                        self._dev_guilds,
                    )
                if resumed := gateway._load_sessions():
                    self._replaying |= resumed
                    user = await self.rest.users.fetch_current()
                    self.user = self.cache._current = user
                gateway._filter(self._dispatch_names())
                gateway._identify_limit(info["session_start_limit"])
                if self._before_identify is not None:
                    gateway._gate_identifies()
                await gateway._start(info["url"], self._presence)
            except (TransportError, StateError):
                if self._closing is None:
                    raise
            finally:
                await self.close()

    async def close(self) -> None:
        """Disconnect, cancel running handlers and close the bridge.

        The shards end their sessions with code 1000, or save them to the session file.
        """
        if self._closing is None or self._closing.done():
            self._closing = asyncio.ensure_future(self._shut_down())
        await asyncio.shield(self._closing)

    async def wait_until_ready(self) -> None:
        """Wait until every shard is ready, as Started says; StateError if it stops."""
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

    def dispatch(self, event: BotEvent) -> None:
        """Send an event of your own to its listeners, waiters and streams."""
        self._emit(event)

    async def update_presence(self, **options: Unpack[PresenceOptions]) -> None:
        """Change what the bot shows on every shard, and on sessions it starts later."""
        check_keys(options, PRESENCE_OPTIONS, "presence options")
        self._presence |= presence_fields(options)
        if self._gateway is not None:
            self._gateway._update_presence(self._presence)

    async def request_members(
        self, guild_id: int, **options: Unpack[MemberRequest]
    ) -> RequestedMembers:
        """Ask the gateway for members of a guild and gather every chunk of the answer.

        Without `query` or `user_ids` it asks for every member. `timeout` bounds the
        wait for each chunk, 30 s by default, and raises TimeoutError when it ends.
        """
        check_keys(options, MEMBER_REQUEST, "member request options")
        request = member_request(guild_id, options, self._intents)
        chunks: list[MembersChunk] = []
        key = (MembersChunk.NAME, request["nonce"])
        wait = options.get("timeout", REQUEST_TIMEOUT)
        async with self._request(
            guild_id, REQUEST_GUILD_MEMBERS, request, key, wait
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
        fields: Iterable[Literal["status", "voice_start_time"]] = CHANNEL_INFO_FIELDS,
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

    async def sync_commands(self) -> list[sync.SyncResult]:
        """Overwrite the commands on Discord now and return what differed."""
        if self.application_id is None:
            raise StateError("sync_commands() needs a running bot")
        return await sync.sync_commands(
            self.http,
            self.application_id,
            [*self.commands.values(), *self.context_menus.values()],
            self._dev_guilds,
            always_put=True,
        )

    async def _run(self, token: str | None) -> None:
        started = asyncio.ensure_future(self.start(token))
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
                await asyncio.wait(stopping, timeout=TASK_CANCEL_TIMEOUT)
        await self.voice.close()
        if self._gateway is not None:
            await self._gateway.close()
        if self._readiness is not None:
            self._readiness.close()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=TASK_CANCEL_TIMEOUT)
        self._running_tasks.clear()
        if self._bridge is not None:
            await self._bridge.close()
        self._bridge = None
        self._ready = False
        self._stop_ready_waiters()
        if self._set_up:
            self._set_up = False
            stopped = Stopped()
            for waiter in self._waiters.get(Stopped.NAME, ()):
                _resolve(waiter, stopped)
            for listener in list(self.listeners.get(Stopped.NAME, ())):
                await self._run_listener(listener, stopped, Stopped.NAME)

    async def _setup(self) -> None:
        self._set_up = True
        starting = Starting()
        for waiter in self._waiters.get(Starting.NAME, ()):
            _resolve(waiter, starting)
        for listener in list(self.listeners.get(Starting.NAME, ())):
            await listener.callback(starting)

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
        if RAW_EVENT_NAME in names:
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
        ):
            names.add(InteractionCreate.NAME)
        return sorted(names)

    def _refresh_filter(self) -> None:
        if self._gateway is not None:
            self._gateway._filter(self._dispatch_names())

    @override
    def _registered(self, listener: Listener | None = None) -> None:
        self._refresh_filter()
        self._run_tasks()
        event = None if listener is None else listener.event
        if listener is None or event is None or not event.INTENTS:
            return
        if not event.INTENTS & self._intents:
            privileged = event.INTENTS & PRIVILEGED_INTENTS == event.INTENTS
            log.warning(
                "%s listens to %s, which needs the %s intent; add it to "
                "Bot(intents=...)%s",
                _python_name(listener.callback),
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
                    log.log(
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
                self.gateway._handle(delivery)
                self._emit(
                    ShardDisconnect(
                        shard_id=shard_id, code=code, reason=reason, resuming=resuming
                    )
                )
            case ("identify", shard_id, request):
                spawn(self._tasks, self._approve_identify(shard_id, request))
            case ("stopped", shard_id, code, reason, _):
                self.gateway._handle(delivery)
                self._emit(ShardStop(shard_id=shard_id, code=code, reason=reason))
            case ("backpressure", shard_id, queued_bytes):
                self.gateway._handle(delivery)
                self._emit(
                    ShardBackpressure(shard_id=shard_id, queued_bytes=queued_bytes)
                )
            case ("invalid_requests", *_):
                self.gateway._handle(delivery)
            case (
                "voice_state"
                | "voice_moved"
                | "voice_dave"
                | "voice_self"
                | "voice_speaking"
                | "voice_clients"
                | "voice_client_left"
                | "voice_stalled"
                | "voice_wake"
                | "voice_marker"
                | "voice_speech"
                | "voice_deaf"
                | "voice_rtcp",
                *_,
            ):
                self.voice._handle(delivery)

    async def _approve_identify(self, shard_id: int, request: int) -> None:
        if self._before_identify is not None:
            try:
                await self._before_identify(shard_id)
            except Exception:
                log.exception(
                    "before_identify raised for shard %d; it identifies anyway",
                    shard_id,
                )
        self.gateway._approve_identify(shard_id, request)

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

    def _emit(self, event: LocalEvent) -> None:
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
                log.exception(
                    "the ListenerError listener %s raised",
                    _python_name(listener.callback),
                )
                return
            if ListenerError.NAME not in self.listeners:
                log.exception(
                    "the listener %s raised on %s",
                    _python_name(listener.callback),
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
        for callback, task in self.tasks.items():
            if callback not in self._running_tasks:
                running = asyncio.ensure_future(task.run(self._task_failed))
                running.add_done_callback(self._tasks.discard)
                self._tasks.add(running)
                self._running_tasks[callback] = running
        for callback in self._running_tasks.keys() - self.tasks.keys():
            self._running_tasks.pop(callback).cancel()

    def _task_failed(self, task: TaskCallback, error: Exception) -> None:
        if TaskError.NAME not in self.listeners:
            log.error("the task %s raised", _python_name(task), exc_info=error)
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
                dispatch.decoded[cls] = self.cache.apply(cached)
        for key in (name, RAW_EVENT_NAME):
            for waiter in self._waiters.get(key, ()):
                _resolve(waiter, dispatch.event(waiter.event))
        if name == InteractionCreate.NAME:
            created = dispatch.event(InteractionCreate)
            if created is not None:
                self._route(created.interaction, received, shard_id)
        for listener in (
            *self.listeners.get(name, ()),
            *self.listeners.get(RAW_EVENT_NAME, ()),
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
                joined, readied = readiness.created(dispatch.shard_id, dispatch.data)
                dispatch.fields["joined"] = joined
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
                    log.warning(
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

    def _route(self, interaction: Interaction, received: float, shard_id: int) -> None:
        anchor = received
        if shard_id in self._replaying:
            age = time.time() * 1000 - ((interaction.id >> 22) + DISCORD_EPOCH_MS)
            if age > STALE_INTERACTION_MS:
                log.warning(
                    "interaction %d was replayed %.1f s after it was created; "
                    "its token is dead, so no handler runs",
                    interaction.id,
                    age / 1000,
                )
                return
            anchor = min(received, asyncio.get_running_loop().time() - age / 1000)
        handler = self._handler(interaction, anchor)
        if handler is None:
            log.debug("no command or component route handles %d", interaction.id)
            return
        delay = asyncio.get_running_loop().time() - received
        if delay > SLOW_HANDLER_START:
            log.warning(
                "interaction %d reached its handler %.1f s after it was received; "
                "something is blocking the event loop",
                interaction.id,
                delay,
            )
        spawn(self._tasks, handler, eager=True)

    def _handler(
        self, interaction: Interaction, received: float
    ) -> Coroutine[Any, Any, None] | None:
        match interaction:
            case ComponentInteraction():
                return self._route_handler(
                    self.match_component(interaction.data.custom_id),
                    partial(ComponentContext, interaction),
                    received,
                )
            case ModalSubmitInteraction():
                return self._route_handler(
                    self.match_modal(interaction.data.custom_id),
                    partial(ModalContext, interaction),
                    received,
                )
            case CommandInteraction():
                return self._command_handler(interaction, received)
            case AutocompleteInteraction():
                return self._autocomplete_handler(interaction, received)
            case UnknownInteraction():
                return None

    def _route_handler(
        self,
        found: tuple[ComponentRoute[...], dict[str, str | int]] | None,
        context: Callable[..., ComponentContext | ModalContext],
        received: float,
    ) -> Coroutine[Any, Any, None] | None:
        if found is None:
            return None
        route, params = found
        ctx = context(
            self,
            received=received,
            ephemeral=False,
            auto_defer=self.auto_defer,
            allowed_mentions=self._allowed_mentions,
        )
        return self._run_handler(ctx, route.router, route.handler, params.copy)

    def _command_handler(
        self, interaction: CommandInteraction, received: float
    ) -> Coroutine[Any, Any, None] | None:
        data = interaction.data
        command: Command | ContextMenuCommand
        found = self._command(data)
        if found is not None:
            command, options = found
            arguments = partial(command.bind, options, data.resolved)
        elif (menu := self.context_menus.get((data.type, data.name))) is not None:
            command, arguments = menu, partial(menu.bind, interaction)
        else:
            return None
        ctx = Context(
            interaction,
            self,
            received=received,
            ephemeral=command.ephemeral,
            auto_defer=command.auto_defer,
            allowed_mentions=self._allowed_mentions,
        )
        return self._run_handler(ctx, command.router, command.callback, arguments)

    def _command(
        self, data: CommandData
    ) -> tuple[Command, tuple[InteractionDataOption, ...]] | None:
        if data.type is not CommandType.CHAT_INPUT:
            return None
        names, options = _split_subcommands(data.options)
        node: Command | Group | None = self.commands.get(data.name)
        for name in names:
            node = node.commands.get(name) if isinstance(node, Group) else None
        return (node, options) if isinstance(node, Command) else None

    def _autocomplete_handler(
        self, interaction: AutocompleteInteraction, received: float
    ) -> Coroutine[Any, Any, None] | None:
        found = self._command(interaction.data)
        if found is None:
            return None
        command, options = found
        focused = next((option for option in options if option.focused), None)
        function = None if focused is None else command.autocompletes.get(focused.name)
        if focused is None or function is None:
            return None
        ctx = AutocompleteContext(interaction, self, received=received)
        return self._autocomplete(ctx, command.router, function, focused.value)

    async def _run_handler(
        self,
        ctx: Context | ComponentContext | ModalContext,
        owner: Router,
        handler: Handler,
        arguments: Callable[[], Mapping[str, Any]],
    ) -> None:
        try:
            with ctx.auto_defer():
                await handler(ctx, **arguments())
        except Exception as error:
            await self._handle_error(ctx, owner, handler, error)
            return
        if ctx.state is ResponseState.PENDING:
            log.warning(
                "%s returned without answering; Discord shows "
                "'The application did not respond'",
                _python_name(handler),
            )
        elif ctx.state is ResponseState.DEFERRED:
            log.warning(
                "%s returned after deferring without answering; the user sees "
                "'thinking' until the interaction expires",
                _python_name(handler),
            )

    async def _autocomplete(
        self,
        ctx: AutocompleteContext,
        owner: Router,
        function: AutocompleteCallback,
        value: Any,
    ) -> None:
        try:
            await ctx._respond(
                autocomplete_choices(await function(ctx, value), function)
            )
        except Exception as error:
            await self._handle_error(ctx, owner, function, error)

    async def _handle_error(
        self,
        ctx: Context | ComponentContext | ModalContext | AutocompleteContext,
        owner: Router,
        handler: Callable[..., object],
        error: Exception,
    ) -> None:
        for hook in owner.find_error_handlers(error):
            try:
                await hook(ctx, error)
            except Exception as raised:
                if raised is error:
                    continue
                log.exception(
                    "the error hook %s raised while handling %r",
                    _python_name(hook),
                    error,
                )
            return
        log.error("%s raised", _python_name(handler), exc_info=error)
        try:
            await _answer_failure(ctx)
        except Exception:
            log.exception("answering interaction %d failed", ctx.interaction.id)


def _resolve_token(token: str | None) -> str:
    token = token or os.environ.get(TOKEN_VARIABLE)
    if not token:
        raise ValueError(f"pass a token or set {TOKEN_VARIABLE}")
    return token.removeprefix("Bot ")


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


async def _answer_failure(
    ctx: Context | ComponentContext | ModalContext | AutocompleteContext,
) -> None:
    if isinstance(ctx, AutocompleteContext):
        if ctx.state is ResponseState.PENDING:
            await ctx._respond([])
    elif ctx.state in (ResponseState.PENDING, ResponseState.DEFERRED_UPDATE):
        await ctx.respond(FAILURE_ANSWER, ephemeral=True)
    elif ctx.state is ResponseState.DEFERRED:
        await ctx.respond(FAILURE_ANSWER)


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
