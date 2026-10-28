import asyncio
import contextlib
import logging
import os
import signal
import threading
import time
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from types import EllipsisType
from typing import Any, cast

import msgspec

from bostie import sync
from bostie._bridge import Bridge, GatewayDelivery
from bostie.cache import Cache
from bostie.commands import (
    AutocompleteCallback,
    Command,
    Group,
    _python_name,
    autocomplete_choices,
)
from bostie.context import (
    AutocompleteContext,
    ComponentContext,
    Context,
    ResponseState,
    _split_subcommands,
)
from bostie.enums import CommandType, Intents, InteractionType
from bostie.errors import TransportError
from bostie.events import (
    EVENTS,
    Event,
    InteractionCreate,
    RawEvent,
    Ready,
    Resumed,
    decode_event,
)
from bostie.file import File
from bostie.gateway import GatewayClient
from bostie.http import HTTPClient
from bostie.models import (
    AllowedMentions,
    Attachment,
    Channel,
    Component,
    Embed,
    Guild,
    Interaction,
    InteractionData,
    InteractionDataOption,
    Member,
    Message,
    User,
    build_message,
    decode,
)
from bostie.models.message import in_guild
from bostie.router import RAW_EVENT_NAME, EventClass, Listener, Router
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
COMMAND_INTERACTIONS = frozenset(
    {
        InteractionType.APPLICATION_COMMAND,
        InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE,
    }
)
COMPONENT_INTERACTIONS = frozenset(
    {InteractionType.MESSAGE_COMPONENT, InteractionType.MODAL_SUBMIT}
)

type Handler = Callable[..., Coroutine[Any, Any, None]]


@dataclass(frozen=True, slots=True)
class _Waiter:
    event: type[EventClass]
    check: Callable[[Any], bool] | None
    future: asyncio.Future[Any]


class Bot(Router):
    """A Discord bot on one gateway shard: commands, components, listeners and REST."""

    def __init__(
        self,
        *,
        intents: Intents = Intents.DEFAULT,
        dev_guilds: Iterable[int] = (),
        sync_commands: bool = True,
        auto_defer: float | None = 2.5,
        cache: bool = True,
        allowed_mentions: AllowedMentions = DEFAULT_ALLOWED_MENTIONS,
        presence: Mapping[str, Any] | None = None,
        global_rate_limit: int = 50,
        api_url: str = "https://discord.com/api/v10",
        voice: Defaults = DEFAULT_VOICE,
    ) -> None:
        super().__init__()
        self.cache = Cache()
        self.voice = VoiceManager(self, voice)
        self.user: User | None = None
        self.application_id: int | None = None
        self._intents = intents
        self._dev_guilds = tuple(dev_guilds)
        self._autosync = sync_commands
        self._auto_defer = auto_defer
        self._caching = cache
        self._allowed_mentions = allowed_mentions
        self._presence = presence
        self._global_rate_limit = global_rate_limit
        self._api_url = api_url
        self._bridge: Bridge | None = None
        self._http: HTTPClient | None = None
        self._gateway: GatewayClient | None = None
        self._closing: asyncio.Task[None] | None = None
        self._ready = False
        self._ready_waiters: list[asyncio.Future[None]] = []
        self._waiters: dict[str, list[_Waiter]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._replaying = False
        self._failed_dispatches: set[str] = set()

    @property
    def bot(self) -> "Bot":
        """This bot, so code written against a Router works on the bot too."""
        return self

    @property
    def http(self) -> HTTPClient:
        """The REST client, for any endpoint; it exists once `start()` runs."""
        if self._http is None:
            raise RuntimeError("bot.http exists once the bot has started")
        return self._http

    @property
    def gateway(self) -> GatewayClient:
        """The gateway connection, for any opcode; it exists once `start()` runs."""
        if self._gateway is None:
            raise RuntimeError("bot.gateway exists once the bot has started")
        return self._gateway

    @property
    def latency(self) -> float | None:
        """Seconds from the last heartbeat to its ACK, or None when there is none."""
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
            raise RuntimeError("the bot is already running")
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
        self._replaying = False
        async with bridge:
            self._http = http = HTTPClient(bridge)
            self._gateway = gateway = GatewayClient(bridge, token)
            try:
                info = await http.request("GET", "/gateway/bot")
                shards = info.get("shards", 1)
                if shards > 1:
                    log.warning(
                        "Discord recommends %d shards; bostie runs one for now",
                        shards,
                    )
                application = await http.request("GET", "/applications/@me")
                self.application_id = int(application["id"])
                if self._autosync:
                    await sync.sync_commands(
                        http,
                        self.application_id,
                        list(self.commands.values()),
                        self._dev_guilds,
                    )
                self._warn_about_privileged_intents()
                gateway.filter(self._dispatch_names())
                gateway.identify_limit(info["session_start_limit"])
                await gateway.start(
                    info["url"],
                    intents=self._intents,
                    presence=self._presence,
                )
            except (TransportError, RuntimeError):
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
        """Wait for the first READY since `start()`; RuntimeError if it ends first."""
        if self._ready:
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._ready_waiters.append(future)
        try:
            await future
        finally:
            self._ready_waiters.remove(future)

    async def wait_for[E: Event | VoiceEvent](
        self,
        event: type[E],
        *,
        check: Callable[[E], bool] | None = None,
    ) -> E:
        """Wait for the next `event` that passes `check`.

        An exception from `check` is raised here; bound the wait with asyncio.timeout().
        """
        waiter = _Waiter(event, check, asyncio.get_running_loop().create_future())
        waiters = self._waiters.setdefault(event.NAME, [])
        waiters.append(waiter)
        if len(waiters) == 1:
            self._refresh_filter()
        try:
            return cast(E, await waiter.future)
        finally:
            waiters.remove(waiter)
            if not waiters:
                del self._waiters[event.NAME]
                self._refresh_filter()

    async def sync_commands(self) -> None:
        """Overwrite the slash commands on Discord now, even when nothing changed."""
        if self.application_id is None:
            raise RuntimeError("sync_commands() needs a running bot")
        await sync.sync_commands(
            self.http,
            self.application_id,
            list(self.commands.values()),
            self._dev_guilds,
            always_put=True,
        )

    async def send_message(
        self,
        channel_id: int,
        content: str | None = None,
        *,
        embeds: Sequence[Embed | Mapping[str, Any]] | None = None,
        components: Sequence[Component | Mapping[str, Any]] | None = None,
        files: Sequence[File] = (),
        allowed_mentions: AllowedMentions | None = None,
        flags: int | None = None,
        tts: bool | None = None,
        reply_to: int | None = None,
    ) -> Message:
        """Send a message; `reply_to` takes the id of a message in the same channel."""
        body, files = build_message(
            content,
            embeds=embeds,
            components=components,
            files=files,
            allowed_mentions=self._allowed_mentions
            if allowed_mentions is None
            else allowed_mentions,
            flags=flags,
            tts=tts,
            reply_to=reply_to,
        )
        data = await self.http._request(
            "POST",
            "/channels/{channel_id}/messages",
            json=body,
            files=files,
            channel_id=channel_id,
        )
        return in_guild(decode(Message, data), self._guild_of(channel_id))

    async def edit_message(
        self,
        channel_id: int,
        message_id: int,
        content: str | None = None,
        *,
        embeds: Sequence[Embed | Mapping[str, Any]] | None = None,
        components: Sequence[Component | Mapping[str, Any]] | None = None,
        files: Sequence[File] = (),
        attachments: Sequence[Attachment | int] = (),
        allowed_mentions: AllowedMentions | None = None,
        flags: int | None = None,
    ) -> Message:
        """Edit a message; only the parts passed change.

        With `files` or `attachments`, only the listed `attachments` stay.
        """
        body, files = build_message(
            content,
            embeds=embeds,
            components=components,
            files=files,
            attachments=attachments,
            allowed_mentions=self._allowed_mentions
            if allowed_mentions is None
            else allowed_mentions,
            flags=flags,
        )
        data = await self.http._request(
            "PATCH",
            "/channels/{channel_id}/messages/{message_id}",
            json=body,
            files=files,
            channel_id=channel_id,
            message_id=message_id,
        )
        return in_guild(decode(Message, data), self._guild_of(channel_id))

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

    async def fetch_message(self, channel_id: int, message_id: int) -> Message:
        data = await self.http._request(
            "GET",
            "/channels/{channel_id}/messages/{message_id}",
            channel_id=channel_id,
            message_id=message_id,
        )
        return in_guild(decode(Message, data), self._guild_of(channel_id))

    async def fetch_channel(self, channel_id: int) -> Channel:
        data = await self.http._request(
            "GET", "/channels/{channel_id}", channel_id=channel_id
        )
        return decode(Channel, data)

    async def fetch_user(self, user_id: int) -> User:
        data = await self.http._request("GET", "/users/{user_id}", user_id=user_id)
        return decode(User, data)

    async def fetch_guild(self, guild_id: int) -> Guild:
        data = await self.http._request("GET", "/guilds/{guild_id}", guild_id=guild_id)
        return decode(Guild, data)

    async def fetch_member(self, guild_id: int, user_id: int) -> Member:
        data = await self.http._request(
            "GET",
            "/guilds/{guild_id}/members/{user_id}",
            guild_id=guild_id,
            user_id=user_id,
        )
        return decode(Member, data)

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
                future.set_exception(RuntimeError("the bot stopped before READY"))

    def _dispatch_names(self) -> list[str] | None:
        names = {*self.listeners, *self._waiters, "RATE_LIMITED"} - LOCAL_NAMES
        if RAW_EVENT_NAME in names:
            return None
        if self._caching:
            names |= Cache.NAMES
        if self.commands or self.component_routes:
            names.add(InteractionCreate.NAME)
        return sorted(names)

    def _refresh_filter(self) -> None:
        if self._gateway is not None:
            self._gateway.filter(self._dispatch_names())

    def _registered(self) -> None:
        self._refresh_filter()

    def _warn_about_privileged_intents(self) -> None:
        for listeners in self.listeners.values():
            for listener in listeners:
                event = cast("type[Event] | None", listener.event)
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
            case ("disconnected", _, _, _, resuming):
                self._replaying = resuming
                self.gateway.handle(delivery)
            case ("stopped" | "backpressure" | "invalid_requests", *_):
                self.gateway.handle(delivery)
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
                | "voice_marker",
                *_,
            ):
                self.voice.handle(delivery, received)

    def _emit(self, event: VoiceEvent) -> None:
        for waiter in self._waiters.get(event.NAME, ()):
            if not waiter.future.done():
                _resolve(waiter, event)
        for listener in self.listeners.get(event.NAME, ()):
            self._spawn(_run_listener(listener, event, event.NAME))

    def _shard_for(self, guild_id: int) -> int:
        return 0

    def _dispatch(
        self, shard_id: int, seq: int, name: str, data: bytes, received: float
    ) -> None:
        decoded: dict[type[Event], Event | None] = {}

        def event[E: Event](cls: type[E]) -> E | None:
            if cls not in decoded:
                decoded[cls] = (
                    RawEvent(shard_id=shard_id, raw_json=data, name=name, seq=seq)
                    if cls is RawEvent
                    else decode_event(cls, shard_id, data)
                )
            return cast("E | None", decoded[cls])

        if name == Ready.NAME:
            self._replaying = False
            ready = event(Ready)
            if ready is not None:
                self.user = ready.user
                if ready.application is not None:
                    self.application_id = ready.application.id
            self._ready = True
            for future in self._ready_waiters:
                if not future.done():
                    future.set_result(None)
        elif name == Resumed.NAME:
            self._replaying = False
        elif name == "RATE_LIMITED":
            limit = msgspec.json.decode(data)
            log.warning(
                "the gateway rate limited opcode %s; retry after %s s",
                limit.get("opcode"),
                limit.get("retry_after"),
            )
        if self._caching and name in Cache.NAMES:
            cached = event(EVENTS[name])
            if cached is not None:
                self.cache.apply(cached)
        for key in (name, RAW_EVENT_NAME):
            for waiter in self._waiters.get(key, ()):
                if not waiter.future.done():
                    _resolve(waiter, event(cast("type[Event]", waiter.event)))
        if name == InteractionCreate.NAME:
            created = event(InteractionCreate)
            if created is not None:
                self._route(created.interaction, received)
        for listener in (
            *self.listeners.get(name, ()),
            *self.listeners.get(RAW_EVENT_NAME, ()),
        ):
            payload = event(
                RawEvent
                if listener.event is None
                else cast("type[Event]", listener.event)
            )
            if payload is not None:
                self._spawn(_run_listener(listener, payload, name))

    def _route(self, interaction: Interaction, received: float) -> None:
        anchor = received
        if self._replaying:
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
        self._spawn(handler)

    def _handler(
        self, interaction: Interaction, received: float
    ) -> Coroutine[Any, Any, None] | None:
        data = interaction.data
        if data is None:
            return None
        if interaction.type in COMPONENT_INTERACTIONS:
            found = self.match_component(data.custom_id or "")
            if found is None:
                return None
            route, params = found
            component = ComponentContext(
                interaction,
                self,
                self.http,
                received=received,
                ephemeral=False,
                auto_defer=self._auto_defer,
                allowed_mentions=self._allowed_mentions,
            )
            return self._run_handler(
                component, route.router, route.handler, params.copy
            )
        found_command = (
            self._command(data) if interaction.type in COMMAND_INTERACTIONS else None
        )
        if found_command is None:
            return None
        command, options = found_command
        if interaction.type is InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE:
            return self._autocomplete_handler(interaction, received, command, options)
        ctx = Context(
            interaction,
            self,
            self.http,
            received=received,
            ephemeral=command.ephemeral,
            auto_defer=self._auto_defer
            if isinstance(command.auto_defer, EllipsisType)
            else command.auto_defer,
            allowed_mentions=self._allowed_mentions,
        )
        return self._run_handler(
            ctx,
            command.router,
            command.callback,
            partial(command.bind, options, data.resolved),
        )

    def _command(
        self, data: InteractionData
    ) -> tuple[Command, tuple[InteractionDataOption, ...]] | None:
        if data.type not in (None, CommandType.CHAT_INPUT) or data.name is None:
            return None
        names, options = _split_subcommands(data.options)
        node: Command | Group | None = self.commands.get(data.name)
        for name in names:
            node = node.commands.get(name) if isinstance(node, Group) else None
        return (node, options) if isinstance(node, Command) else None

    def _autocomplete_handler(
        self,
        interaction: Interaction,
        received: float,
        command: Command,
        options: Sequence[InteractionDataOption],
    ) -> Coroutine[Any, Any, None] | None:
        focused = next((option for option in options if option.focused), None)
        function = (
            None if focused is None else command.autocompletes.get(focused.name or "")
        )
        if focused is None or function is None:
            return None
        ctx = AutocompleteContext(
            interaction,
            self,
            self.http,
            received=received,
            ephemeral=False,
            auto_defer=None,
            allowed_mentions=None,
        )
        return self._autocomplete(ctx, command.router, function, focused.value)

    async def _run_handler(
        self,
        ctx: Context,
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
            await ctx.respond(
                autocomplete_choices(await function(ctx, value), function)
            )
        except Exception as error:
            await self._handle_error(ctx, owner, function, error)

    async def _handle_error(
        self,
        ctx: Context | AutocompleteContext,
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

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.Task(
            coroutine, loop=asyncio.get_running_loop(), eager_start=True
        )
        if not task.done():
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)


def _resolve_token(token: str | None) -> str:
    token = token or os.environ.get(TOKEN_VARIABLE)
    if not token:
        raise ValueError(f"pass a token or set {TOKEN_VARIABLE}")
    return token.removeprefix("Bot ")


def _resolve(waiter: _Waiter, event: Event | VoiceEvent | None) -> None:
    if event is None:
        return
    try:
        matched = waiter.check is None or waiter.check(event)
    except Exception as error:
        waiter.future.set_exception(error)
        return
    if matched:
        waiter.future.set_result(event)


async def _run_listener(
    listener: Listener, event: Event | VoiceEvent, name: str
) -> None:
    try:
        await listener.callback(event)
    except Exception:
        log.exception(
            "the listener %s raised on %s", _python_name(listener.callback), name
        )


async def _answer_failure(ctx: Context | AutocompleteContext) -> None:
    if ctx.state is ResponseState.PENDING:
        if isinstance(ctx, AutocompleteContext):
            await ctx.respond([])
        else:
            await ctx.respond(FAILURE_ANSWER, ephemeral=True)
    elif ctx.state is ResponseState.DEFERRED and isinstance(ctx, Context):
        await ctx.respond(FAILURE_ANSWER)
