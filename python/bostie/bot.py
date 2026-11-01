import asyncio
import contextlib
import logging
import os
import signal
import threading
import time
from collections.abc import Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any, Literal, Unpack, cast, override

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
from bostie.errors import MemberRequestRateLimited, StateError, TransportError
from bostie.events import (
    EVENTS,
    Event,
    GatewayRateLimited,
    InteractionCreate,
    MembersChunk,
    RawEvent,
    Ready,
    Resumed,
    UserUpdate,
    decode_event,
)
from bostie.gateway import (
    MEMBER_REQUEST,
    MEMBER_REQUEST_TIMEOUT,
    PRESENCE_OPTIONS,
    REQUEST_GUILD_MEMBERS,
    GatewayClient,
    MemberRequest,
    PresenceOptions,
    RequestedMembers,
    member_request,
    presence_fields,
)
from bostie.http import HTTPClient
from bostie.models import (
    AllowedMentions,
    AutocompleteInteraction,
    Channel,
    CommandData,
    CommandInteraction,
    ComponentInteraction,
    EditParts,
    Guild,
    Interaction,
    InteractionDataOption,
    Member,
    Message,
    MessageParts,
    ModalSubmitInteraction,
    UnknownInteraction,
    User,
    WebhookMessage,
    build_message,
    decode,
    decode_message,
)
from bostie.models.base import check_keys
from bostie.models.message import MESSAGE_PARTS, in_guild, message_body
from bostie.router import RAW_EVENT_NAME, AnyEvent, Listener, Router
from bostie.voice.connection import VoiceManager
from bostie.voice.events import LOCAL_NAMES, VoiceEvent
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
DEFAULT_ALLOWED_MENTIONS = AllowedMentions(parse=("users", "roles"))
DEFAULT_VOICE = Defaults()

type Handler = Callable[..., Coroutine[Any, Any, None]]
type MemberAnswer = MembersChunk | GatewayRateLimited


@dataclass(frozen=True, slots=True)
class _Waiter[E: AnyEvent]:
    event: type[E]
    check: Callable[[E], bool] | None
    future: asyncio.Future[E]


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
            self.decoded[cls] = (
                RawEvent(
                    shard_id=self.shard_id,
                    raw_json=self.data,
                    name=self.name,
                    seq=self.seq,
                )
                if cls is RawEvent
                else decode_event(cls, self.shard_id, self.data)
            )
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
        allowed_mentions: AllowedMentions = DEFAULT_ALLOWED_MENTIONS,
        status: Status = Status.ONLINE,
        activities: Iterable[ActivityDict] = (),
        afk: bool = False,
        idle_since: datetime | None = None,
        global_rate_limit: int = 50,
        api_url: str = "https://discord.com/api/v10",
        voice: Defaults = DEFAULT_VOICE,
        shard_count: int | Literal["auto"] = "auto",
        shard_ids: Iterable[int] | Literal["all"] = "all",
    ) -> None:
        super().__init__()
        self._shard_count = shard_count
        self._shard_ids = _shard_ids(shard_count, shard_ids)
        self.cache = Cache()
        self.voice = VoiceManager(self, voice)
        self.user: User | None = None
        self.application_id: int | None = None
        self._intents = intents
        self._dev_guilds = tuple(dev_guilds)
        self._auto_sync = auto_sync
        self.auto_defer = auto_defer
        self._caching = cache
        self._allowed_mentions = allowed_mentions
        self._presence = presence_fields(
            {
                "status": status,
                "activities": activities,
                "afk": afk,
                "idle_since": idle_since,
            }
        )
        self._global_rate_limit = global_rate_limit
        self._api_url = api_url
        self._bridge: Bridge | None = None
        self._http: HTTPClient | None = None
        self._gateway: GatewayClient | None = None
        self._closing: asyncio.Task[None] | None = None
        self._ready = False
        self._unready: set[int] = set()
        self._ready_waiters: list[asyncio.Future[None]] = []
        self._waiters: dict[str, list[_Waiter[Any]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._replaying: set[int] = set()
        self._failed_dispatches: set[str] = set()
        self._member_requests: dict[str, asyncio.Queue[MemberAnswer]] = {}

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
    def gateway(self) -> GatewayClient:
        """The gateway connection, for any opcode; it exists once `start()` runs."""
        if self._gateway is None:
            raise StateError("bot.gateway exists once the bot has started")
        return self._gateway

    @property
    def latency(self) -> float | None:
        """Mean seconds from a heartbeat to its ACK over the shards, or None."""
        return None if self._gateway is None else self._gateway.latency

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
                global_limit=self._global_rate_limit,
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
                )
                self._unready = set(gateway.shard_ids)
                application = await http.request("GET", "/applications/@me")
                self.application_id = int(application["id"])
                if self._auto_sync:
                    await sync.sync_commands(
                        http,
                        self.application_id,
                        [*self.commands.values(), *self.context_menus.values()],
                        self._dev_guilds,
                    )
                self._warn_about_privileged_intents()
                gateway._filter(self._dispatch_names())
                gateway._identify_limit(info["session_start_limit"])
                await gateway._start(info["url"], self._presence)
            except (TransportError, StateError):
                if self._closing is None:
                    raise
            finally:
                await self.close()

    async def close(self) -> None:
        """Disconnect with code 1000, cancel running handlers and close the bridge."""
        if self._closing is None or self._closing.done():
            self._closing = asyncio.ensure_future(self._shut_down())
        await asyncio.shield(self._closing)

    async def wait_until_ready(self) -> None:
        """Wait until every shard got READY since `start()`; StateError if it ends."""
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
        future: asyncio.Future[E] = asyncio.get_running_loop().create_future()
        waiter = _Waiter(event, check, future)
        waiters = self._waiters.setdefault(event.NAME, [])
        waiters.append(waiter)
        if len(waiters) == 1:
            self._refresh_filter()
        try:
            return await waiter.future
        finally:
            waiters.remove(waiter)
            if not waiters:
                del self._waiters[event.NAME]
                self._refresh_filter()

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
        gateway = self.gateway
        answers: asyncio.Queue[MemberAnswer] = asyncio.Queue()
        chunks: list[MembersChunk] = []
        self._member_requests[request["nonce"]] = answers
        self._refresh_filter()
        try:
            gateway.send(REQUEST_GUILD_MEMBERS, request)
            while not chunks or len(chunks) < chunks[-1].chunk_count:
                async with asyncio.timeout(
                    options.get("timeout", MEMBER_REQUEST_TIMEOUT)
                ):
                    answer = await answers.get()
                if isinstance(answer, GatewayRateLimited):
                    raise MemberRequestRateLimited(answer.retry_after)
                chunks.append(answer)
        finally:
            del self._member_requests[request["nonce"]]
            self._refresh_filter()
        return RequestedMembers(
            members=tuple(member for chunk in chunks for member in chunk.members),
            presences=tuple(
                presence for chunk in chunks for presence in chunk.presences
            ),
            not_found=tuple(user_id for chunk in chunks for user_id in chunk.not_found),
        )

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

    async def send_message(
        self,
        channel_id: int,
        content: str | None = None,
        *,
        reply_to: int | None = None,
        **parts: Unpack[MessageParts],
    ) -> Message:
        """Send a message; `reply_to` takes the id of a message in the same channel."""
        check_keys(parts, MESSAGE_PARTS, "message parts")
        parts.setdefault("allowed_mentions", self._allowed_mentions)
        body, files = message_body(content, parts, reply_to=reply_to)
        message = await self.http._decoded(
            partial(decode, Message),
            "POST",
            "/channels/{channel_id}/messages",
            json=body,
            files=files,
            channel_id=channel_id,
        )
        return in_guild(message, self._guild_of(channel_id))

    async def edit_message(
        self,
        channel_id: int,
        message_id: int,
        content: str | None = None,
        **parts: Unpack[EditParts],
    ) -> Message | WebhookMessage:
        """Edit a message; only the parts passed change.

        With `files` or `attachments`, only the listed `attachments` stay.
        """
        parts.setdefault("allowed_mentions", self._allowed_mentions)
        body, files = build_message(content, **parts)
        message = await self.http._decoded(
            decode_message,
            "PATCH",
            "/channels/{channel_id}/messages/{message_id}",
            json=body,
            files=files,
            channel_id=channel_id,
            message_id=message_id,
        )
        return in_guild(message, self._guild_of(channel_id))

    async def delete_message(
        self, channel_id: int, message_id: int, *, reason: str | None = None
    ) -> None:
        await self.http._request(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}",
            reason=reason,
            channel_id=channel_id,
            message_id=message_id,
        )

    async def fetch_message(
        self, channel_id: int, message_id: int
    ) -> Message | WebhookMessage:
        message = await self.http._decoded(
            decode_message,
            "GET",
            "/channels/{channel_id}/messages/{message_id}",
            channel_id=channel_id,
            message_id=message_id,
        )
        return in_guild(message, self._guild_of(channel_id))

    async def fetch_channel(self, channel_id: int) -> Channel:
        return await self.http._decoded(
            partial(decode, Channel),
            "GET",
            "/channels/{channel_id}",
            channel_id=channel_id,
        )

    async def fetch_user(self, user_id: int) -> User:
        return await self.http._decoded(
            partial(decode, User), "GET", "/users/{user_id}", user_id=user_id
        )

    async def fetch_guild(self, guild_id: int) -> Guild:
        return await self.http._decoded(
            partial(decode, Guild), "GET", "/guilds/{guild_id}", guild_id=guild_id
        )

    async def fetch_member(self, guild_id: int, user_id: int) -> Member:
        return await self.http._decoded(
            partial(decode, Member),
            "GET",
            "/guilds/{guild_id}/members/{user_id}",
            guild_id=guild_id,
            user_id=user_id,
        )

    def _guild_of(self, channel_id: int) -> int | None:
        channel = self.cache.channel(channel_id)
        return None if channel is None else channel.guild_id

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
        await self.voice.close()
        if self._gateway is not None:
            await self._gateway.close()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=TASK_CANCEL_TIMEOUT)
        if self._bridge is not None:
            await self._bridge.close()
        self._bridge = None
        self._ready = False
        self._stop_ready_waiters()

    def _stop_ready_waiters(self) -> None:
        for future in self._ready_waiters:
            if not future.done():
                future.set_exception(StateError("the bot stopped before READY"))

    def _dispatch_names(self) -> list[str] | None:
        names = {*self.listeners, *self._waiters, GatewayRateLimited.NAME} - LOCAL_NAMES
        if RAW_EVENT_NAME in names:
            return None
        if self._member_requests:
            names.add(MembersChunk.NAME)
        if self._caching:
            names |= Cache.NAMES
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
    def _registered(self) -> None:
        self._refresh_filter()

    def _warn_about_privileged_intents(self) -> None:
        for listeners in self.listeners.values():
            for listener in listeners:
                event = listener.event
                if event is None:
                    continue
                missing = event.INTENTS & PRIVILEGED_INTENTS & ~self._intents
                if missing:
                    log.warning(
                        "%s listens to %s, which needs the %s intent; enable it in "
                        "Bot(intents=...) and in the Developer Portal",
                        _python_name(listener.callback),
                        event.NAME,
                        missing.name,
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
            case ("disconnected", shard_id, _, _, resuming):
                if resuming:
                    self._replaying.add(shard_id)
                else:
                    self._replaying.discard(shard_id)
                self.gateway._handle(delivery)
            case ("stopped" | "backpressure" | "invalid_requests", *_):
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
                | "voice_deaf",
                *_,
            ):
                self.voice._handle(delivery)

    def _emit(self, event: VoiceEvent) -> None:
        for waiter in self._waiters.get(event.NAME, ()):
            if not waiter.future.done():
                _resolve(waiter, event)
        for listener in self.listeners.get(event.NAME, ()):
            spawn(self._tasks, _run_listener(listener, event, event.NAME), eager=True)

    def _dispatch(
        self, shard_id: int, seq: int, name: str, data: bytes, received: float
    ) -> None:
        dispatch = _Dispatch(shard_id, seq, name, data)
        self._follow_session(dispatch)
        self._answer_member_request(dispatch)
        if self._caching and name in Cache.NAMES:
            cached = dispatch.event(EVENTS[name])
            if cached is not None:
                self.cache.apply(cached)
        for key in (name, RAW_EVENT_NAME):
            for waiter in self._waiters.get(key, ()):
                if not waiter.future.done():
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
                spawn(self._tasks, _run_listener(listener, payload, name), eager=True)

    def _follow_session(self, dispatch: _Dispatch) -> None:
        if dispatch.name == Ready.NAME:
            self._replaying.discard(dispatch.shard_id)
            ready = dispatch.event(Ready)
            if ready is not None:
                self.user = ready.user
                self.application_id = ready.application.id
            self._unready.discard(dispatch.shard_id)
            if not self._unready and not self._ready:
                self._ready = True
                for future in self._ready_waiters:
                    if not future.done():
                        future.set_result(None)
        elif dispatch.name == Resumed.NAME:
            self._replaying.discard(dispatch.shard_id)
        elif dispatch.name == UserUpdate.NAME:
            update = dispatch.event(UserUpdate)
            if update is not None:
                self.user = update.user

    def _answer_member_request(self, dispatch: _Dispatch) -> None:
        answer: MemberAnswer | None = None
        nonce: str | None = None
        if dispatch.name == MembersChunk.NAME:
            answer = dispatch.event(MembersChunk)
            nonce = None if answer is None else answer.nonce
        elif dispatch.name == GatewayRateLimited.NAME:
            answer = dispatch.event(GatewayRateLimited)
            meta = None if answer is None else answer.meta
            nonce = None if meta is None else meta.nonce
        answers = None if nonce is None else self._member_requests.get(nonce)
        if answers is not None and answer is not None:
            answers.put_nowait(answer)
        elif isinstance(answer, GatewayRateLimited):
            log.warning(
                "the gateway rate limited opcode %s; retry after %s s",
                answer.opcode,
                answer.retry_after,
            )

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
    if event is None:
        return
    try:
        matched = waiter.check is None or waiter.check(event)
    except Exception as error:
        waiter.future.set_exception(error)
        return
    if matched:
        waiter.future.set_result(event)


async def _run_listener(listener: Listener, event: AnyEvent, name: str) -> None:
    try:
        await listener.callback(event)
    except Exception:
        log.exception(
            "the listener %s raised on %s", _python_name(listener.callback), name
        )


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
