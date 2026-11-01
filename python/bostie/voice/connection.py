import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, Unpack, overload

from bostie import _native
from bostie._aio import spawn
from bostie.cache import Cache
from bostie.enums import (
    ChannelType,
    Direction,
    DisconnectReason,
    Intents,
    SpeakingFlags,
    State,
)
from bostie.errors import NotFound, NotInVoice, VoiceConnectError, VoiceNotConnected
from bostie.events import Event, VoiceServerUpdate, VoiceStateUpdate
from bostie.gateway import SHARD, GatewayClient
from bostie.http import HTTPClient
from bostie.models import Channel, User
from bostie.voice import layers
from bostie.voice.encoder import DEFAULT_BITRATE, percent
from bostie.voice.events import (
    DaveTransition,
    MediaResumed,
    MediaStalled,
    Moved,
    SpeakingUpdate,
    SpeechStart,
    SpeechStop,
    StateChange,
    UserJoin,
    UserLeave,
    VoiceEvent,
)
from bostie.voice.formats import (
    PCM,
    Defaults,
    EncoderSettings,
    Format,
    Latency,
    Opus,
    Packets,
    ReceiveFormat,
)
from bostie.voice.frames import Frame, Packet
from bostie.voice.receiver import NativeReceiver, Receiver
from bostie.voice.sink import NativeSink, Sink
from bostie.voice.sources import NativeSource

if TYPE_CHECKING:
    from bostie.voice.sources import Source

log = logging.getLogger("bostie.voice")

CLOSE_TIMEOUT = 2.0
UNKNOWN_VOICE_STATE = 10065
VOICE_STATE_UPDATE = 4
VOICE_CHANNELS = frozenset({ChannelType.GUILD_VOICE, ChannelType.GUILD_STAGE_VOICE})
DEFAULT_PCM = PCM()

type StageRole = Literal["listen", "request", "speak"]
type VoiceDelivery = (
    _native.VoiceStateDelivery
    | _native.VoiceMovedDelivery
    | _native.VoiceDaveDelivery
    | _native.VoiceSelfDelivery
    | _native.VoiceSpeakingDelivery
    | _native.VoiceClientsDelivery
    | _native.VoiceClientLeftDelivery
    | _native.VoiceStalledDelivery
    | _native.VoiceWakeDelivery
    | _native.VoiceMarkerDelivery
    | _native.VoiceSpeechDelivery
    | _native.VoiceDeafDelivery
)


class VoiceCore(Protocol):
    def voice_connect(
        self,
        guild_id: int,
        channel_id: int,
        *,
        shard_id: int,
        user_id: int,
        self_mute: bool,
        self_deaf: bool,
        timeout: float,
        server_change_delay: float,
        bitrate: int,
        application: Literal["audio", "voip", "lowdelay"],
        fec: bool,
        packet_loss: int,
        complexity: int,
        speaking: int,
    ) -> None: ...
    def voice_update(
        self, guild_id: int, channel_id: int, self_mute: bool, self_deaf: bool
    ) -> None: ...
    def voice_disconnect(self, guild_id: int) -> None: ...
    def voice_reconnect(self, guild_id: int) -> None: ...
    def voice_encoder(
        self,
        guild_id: int,
        *,
        bitrate: int,
        application: Literal["audio", "voip", "lowdelay"],
        fec: bool,
        packet_loss: int,
        complexity: int,
    ) -> None: ...
    def voice_speaking(self, guild_id: int, flags: int) -> None: ...
    def voice_latency(self, guild_id: int) -> tuple[float | None, float | None]: ...
    def voice_dave(self, guild_id: int) -> tuple[int, int | None, str | None]: ...
    def voice_verification_code(self, guild_id: int, user_id: int) -> str: ...
    def voice_sink(
        self,
        guild_id: int,
        *,
        kind: Literal["pcm", "opus"],
        rate: int,
        channels: int,
        sample: Literal["s16le", "f32le"],
        buffer: float,
        volume: float,
        stall: Literal["hold", "drop"],
    ) -> NativeSink: ...
    def voice_file(self, path: str) -> NativeSource: ...
    def voice_bytes(self, data: bytes, hint: str | None) -> NativeSource: ...
    def voice_url(self, url: str, headers: list[tuple[str, str]]) -> NativeSource: ...
    def voice_receive(
        self,
        guild_id: int,
        *,
        kind: Literal["pcm", "opus", "packets"],
        rate: int,
        channels: int,
        sample: Literal["s16le", "f32le"],
        users: list[int] | None,
        exclude: list[int],
        include_self: bool,
        buffer: float,
        jitter: float,
        markers: bool,
        mix: bool,
        overflow: Literal["drop_oldest", "drop_newest"],
        stall: Literal["wait", "end"],
    ) -> NativeReceiver: ...
    def voice_speech_hangover(self, guild_id: int, seconds: float) -> None: ...
    def voice_speech_activity(self, guild_id: int, enabled: bool) -> None: ...
    def voice_ssrcs(self, guild_id: int) -> dict[int, int]: ...


class ConnectOptions(TypedDict, total=False):
    """Settings for one connect() call; each overrides voice.Defaults unless None."""

    self_deaf: bool | None
    self_mute: bool | None
    timeout: float | None
    server_change_delay: float | None
    encoder: EncoderSettings | None
    speaking_flags: SpeakingFlags | None
    stage: StageRole | None
    speech_hangover: float | None
    speech_activity: bool | None


class _HasCore(Protocol):
    @property
    def core(self) -> VoiceCore: ...


class VoiceHost(Protocol):
    @property
    def user(self) -> User | None: ...
    @property
    def cache(self) -> Cache: ...
    @property
    def http(self) -> HTTPClient: ...
    @property
    def gateway(self) -> GatewayClient: ...
    @property
    def _intents(self) -> Intents: ...
    @property
    def _bridge(self) -> _HasCore | None: ...
    async def fetch_channel(self, channel_id: int) -> Channel: ...
    async def wait_for[E: Event](
        self, event: type[E], *, check: Callable[[E], bool] | None = None
    ) -> E: ...
    def _emit(self, event: VoiceEvent) -> None: ...


class _Woken(Protocol):
    def _wake(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Dave:
    """The call's end-to-end encryption; `protocol_version` 0 means none."""

    protocol_version: int
    epoch: int | None
    privacy_code: str | None
    _connection: "VoiceConnection" = field(repr=False, compare=False)

    async def verification_code(self, user_id: int) -> str:
        """The code to compare with `user_id` out of band to verify their identity."""
        return await self._connection._verification_code(user_id)


@dataclass(frozen=True, slots=True)
class ExternalVoice:
    """The session and server an external voice engine connects with.

    bostie has no media in that guild; later VoiceServerUpdate events are the
    external engine's to follow.
    """

    state: VoiceStateUpdate
    server: VoiceServerUpdate
    _manager: "VoiceManager" = field(repr=False, compare=False)
    _guild_id: int = field(repr=False, compare=False)

    async def disconnect(self) -> None:
        """Leave the voice channel."""
        self._manager._send_voice_state(self._guild_id, None, False, False)


class VoiceManager:
    """The bot's voice connections, at most one per guild."""

    def __init__(self, bot: VoiceHost, defaults: Defaults) -> None:
        self.defaults = defaults
        self._bot = bot
        self._connections: dict[int, VoiceConnection] = {}
        self._handles: dict[int, _Woken] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def connections(self) -> tuple["VoiceConnection", ...]:
        return tuple(self._connections.values())

    def get(self, guild_id: int) -> "VoiceConnection | None":
        return self._connections.get(guild_id)

    async def connect(
        self, channel_id: int, **options: Unpack[ConnectOptions]
    ) -> "VoiceConnection":
        """Connect to a voice or stage channel and return once media can flow.

        In a guild where the bot is already connected, this moves it.
        """
        user = self._running_user("bot.voice.connect()")
        settings = self._settings(**options)
        guild_id, channel = await self._voice_channel(channel_id)
        vc = self._connections.get(guild_id)
        if vc is None:
            vc = VoiceConnection(self, guild_id, channel, settings)
            await vc._start(user.id)
            entered = True
        else:
            entered = await vc._join(
                channel, settings.self_mute, settings.self_deaf, settings.timeout
            )
        if (
            entered
            and channel.type is ChannelType.GUILD_STAGE_VOICE
            and settings.stage != "listen"
        ):
            try:
                await vc._request_stage(settings.stage)
            except BaseException:
                await vc.disconnect()
                raise
        return vc

    async def connect_external(
        self,
        guild_id: int,
        channel_id: int,
        *,
        self_deaf: bool | None = None,
        self_mute: bool | None = None,
        timeout: float | None = None,
    ) -> ExternalVoice:
        """Join a voice channel for an external voice engine and return its credentials.

        bostie then has no media in that guild, and later VoiceServerUpdate events
        are the external engine's to follow.
        """
        user = self._running_user("bot.voice.connect_external()")
        if guild_id in self._connections:
            raise RuntimeError(
                f"bostie already runs a voice connection in guild {guild_id}"
            )
        settings = self._settings(
            self_deaf=self_deaf, self_mute=self_mute, timeout=timeout
        )
        loop = asyncio.get_running_loop()
        state = asyncio.Task(
            self._bot.wait_for(
                VoiceStateUpdate,
                check=lambda event: (
                    event.voice_state.user_id == user.id
                    and event.voice_state.guild_id == guild_id
                    and event.voice_state.channel_id == channel_id
                ),
            ),
            loop=loop,
            eager_start=True,
        )
        server = asyncio.Task(
            self._bot.wait_for(
                VoiceServerUpdate, check=lambda event: event.guild_id == guild_id
            ),
            loop=loop,
            eager_start=True,
        )
        try:
            self._send_voice_state(
                guild_id, channel_id, settings.self_mute, settings.self_deaf
            )
            async with asyncio.timeout(settings.timeout):
                return ExternalVoice(await state, await server, self, guild_id)
        except BaseException as error:
            with contextlib.suppress(RuntimeError):
                self._send_voice_state(guild_id, None, False, False)
            if isinstance(error, TimeoutError):
                raise VoiceConnectError(
                    DisconnectReason.TIMEOUT,
                    None,
                    "no VOICE_STATE_UPDATE and VOICE_SERVER_UPDATE in "
                    f"{settings.timeout} s (the channel may be full or the bot "
                    "lacks CONNECT)",
                ) from None
            raise
        finally:
            state.cancel()
            server.cancel()

    async def close(self) -> None:
        """Disconnect every connection, waiting up to 2 s for them to end."""
        connections = self.connections
        await asyncio.gather(
            *(vc.disconnect() for vc in connections), return_exceptions=True
        )
        for vc in connections:
            if vc.state is not State.DISCONNECTED:
                vc._set_state("disconnected", "closed", None, "the bot closed")
        for task in self._tasks:
            task.cancel()

    def _handle(self, delivery: VoiceDelivery) -> None:
        match delivery:
            case ("voice_wake", handle):
                target = self._handles.get(handle)
                if target is not None:
                    target._wake()
            case ("voice_marker", handle, marker, played):
                sink = self._handles.get(handle)
                if isinstance(sink, Sink):
                    sink._marked(marker, played)
            case _:
                vc = self._connections.get(delivery[1])
                if vc is not None:
                    vc._apply(delivery)

    def _running_user(self, call: str) -> User:
        user = self._bot.user
        if user is None or self._bot._bridge is None:
            raise RuntimeError(f"{call} needs a running bot")
        if not self._bot._intents & Intents.GUILD_VOICE_STATES:
            raise RuntimeError(
                "voice needs Intents.GUILD_VOICE_STATES; add it to Bot(intents=...)"
            )
        return user

    def _core(self) -> VoiceCore:
        bridge = self._bot._bridge
        if bridge is None:
            raise RuntimeError("voice needs a running bot")
        return bridge.core

    def _settings(self, **overrides: Any) -> Defaults:
        return replace(
            self.defaults,
            **{name: value for name, value in overrides.items() if value is not None},
        )

    async def _voice_channel(self, channel_id: int) -> tuple[int, Channel]:
        channel = self._bot.cache.channel(channel_id) or await self._bot.fetch_channel(
            channel_id
        )
        if channel.guild_id is None or channel.type not in VOICE_CHANNELS:
            raise ValueError(f"channel {channel_id} is not a voice or stage channel")
        return channel.guild_id, channel

    def _send_voice_state(
        self, guild_id: int, channel_id: int | None, self_mute: bool, self_deaf: bool
    ) -> None:
        self._bot.gateway.send(
            VOICE_STATE_UPDATE,
            {
                "guild_id": str(guild_id),
                "channel_id": None if channel_id is None else str(channel_id),
                "self_mute": self_mute,
                "self_deaf": self_deaf,
            },
        )


class VoiceConnection:
    """The bot's voice connection in one guild."""

    def __init__(
        self,
        manager: VoiceManager,
        guild_id: int,
        channel: Channel,
        settings: Defaults,
    ) -> None:
        self.guild_id = guild_id
        self.manager = manager
        self._settings = settings
        self._channel_id = channel.id
        self._stage_channel = channel.type is ChannelType.GUILD_STAGE_VOICE
        self._channel_bitrate = channel.bitrate
        self._state = State.CONNECTING
        self._reason: DisconnectReason | None = None
        self._code: int | None = None
        self._detail = ""
        self._requested = (settings.self_mute, settings.self_deaf)
        self._self_mute, self._self_deaf = self._requested
        self._mute = False
        self._suppress = False
        self._deaf = False
        self._members: frozenset[int] = frozenset()
        self._encoder = settings.encoder
        self._speaking_flags = settings.speaking_flags
        self._speech_hangover = settings.speech_hangover
        self._speech_activity = settings.speech_activity
        self._speaking: dict[int, float] = {}
        self._speaking_view = MappingProxyType(self._speaking)
        self._speech_ssrcs: dict[int, int] = {}
        self._ssrcs: Mapping[int, int] = MappingProxyType({})
        self._dave = Dave(0, None, None, self)
        self._sinks: set[Sink] = set()
        self._receivers: set[Receiver[Any]] = set()
        self._watchers: list[tuple[Callable[[], bool], asyncio.Future[bool]]] = []
        self._warned = False

    @property
    def channel_id(self) -> int:
        return self._channel_id

    @property
    def state(self) -> State:
        return self._state

    @property
    def self_mute(self) -> bool:
        """The bot's own mute, as Discord applied it."""
        return self._self_mute

    @property
    def self_deaf(self) -> bool:
        """The bot's own deafen, as Discord applied it."""
        return self._self_deaf

    @property
    def mute(self) -> bool:
        """Whether the server muted the bot."""
        return self._mute

    @property
    def suppress(self) -> bool:
        """Whether the bot may not speak on this stage."""
        return self._suppress

    @property
    def deaf(self) -> bool:
        """Whether a moderator deafened the bot (it then receives nothing)."""
        return self._deaf

    @property
    def members(self) -> frozenset[int]:
        """The other users in the call."""
        return self._members

    @property
    def speaking(self) -> Mapping[int, float]:
        """Users talking now, mapped to the time.monotonic() they started."""
        return self._speaking_view

    @property
    def ssrcs(self) -> Mapping[int, int]:
        """Each SSRC in the call mapped to its user id."""
        if self._state is not State.DISCONNECTED:
            self._ssrcs = MappingProxyType(self._core().voice_ssrcs(self.guild_id))
        return self._ssrcs

    @property
    def latency(self) -> Latency:
        if self._state is State.DISCONNECTED:
            return Latency(None, None)
        return Latency(*self._core().voice_latency(self.guild_id))

    @property
    def dave(self) -> Dave:
        if self._state is not State.DISCONNECTED:
            self._dave = Dave(*self._core().voice_dave(self.guild_id), self)
        return self._dave

    @property
    def encoder(self) -> EncoderSettings:
        return self._encoder

    @encoder.setter
    def encoder(self, encoder: EncoderSettings) -> None:
        self._check_live()
        self._apply_encoder(encoder)
        self._encoder = encoder

    @property
    def speaking_flags(self) -> SpeakingFlags:
        return self._speaking_flags

    @speaking_flags.setter
    def speaking_flags(self, flags: SpeakingFlags) -> None:
        self._check_live()
        if not flags:
            raise ValueError("speaking_flags must set at least one flag")
        self._core().voice_speaking(self.guild_id, int(flags))
        self._speaking_flags = SpeakingFlags(flags)

    @property
    def speech_hangover(self) -> float:
        """Seconds without speech before a user counts as stopped."""
        return self._speech_hangover

    @speech_hangover.setter
    def speech_hangover(self, seconds: float) -> None:
        self._check_live()
        self._core().voice_speech_hangover(self.guild_id, seconds)
        self._speech_hangover = seconds

    @property
    def speech_activity(self) -> bool:
        """Whether speech events fire; off with no receiver, nothing is decrypted."""
        return self._speech_activity

    @speech_activity.setter
    def speech_activity(self, enabled: bool) -> None:
        self._check_live()
        self._core().voice_speech_activity(self.guild_id, enabled)
        self._speech_activity = enabled

    def sink(
        self,
        format: Format = DEFAULT_PCM,
        *,
        buffer: float | None = None,
        volume: float = 1.0,
        stall: Literal["hold", "drop"] | None = None,
    ) -> Sink:
        """Open a bounded writer whose audio is mixed into this connection."""
        self._check_live()
        buffer = self._settings.sink_buffer if buffer is None else buffer
        stall = self._settings.stall if stall is None else stall
        pcm = DEFAULT_PCM if isinstance(format, Opus) else format
        native = self._core().voice_sink(
            self.guild_id,
            kind="opus" if isinstance(format, Opus) else "pcm",
            rate=pcm.rate,
            channels=pcm.channels,
            sample=pcm.sample,
            buffer=buffer,
            volume=volume,
            stall=stall,
        )
        sink = Sink(self, native, format, buffer=buffer, stall=stall, volume=volume)
        self._sinks.add(sink)
        self.manager._handles[native.id] = sink
        return sink

    @overload
    def receive(
        self,
        format: Packets,
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        buffer: float | None = None,
        markers: Literal[False] = False,
        overflow: Literal["drop_oldest", "drop_newest"] | None = None,
        stall: Literal["wait", "end"] | None = None,
    ) -> Receiver[Packet]: ...
    @overload
    def receive(
        self,
        format: Packets,
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        buffer: float | None = None,
        markers: Literal[True],
        overflow: Literal["drop_oldest", "drop_newest"] | None = None,
        stall: Literal["wait", "end"] | None = None,
    ) -> Receiver[Packet | SpeechStart | SpeechStop]: ...
    @overload
    def receive(
        self,
        format: Packets,
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        buffer: float | None = None,
        markers: bool,
        overflow: Literal["drop_oldest", "drop_newest"] | None = None,
        stall: Literal["wait", "end"] | None = None,
    ) -> Receiver[Packet | SpeechStart | SpeechStop]: ...
    @overload
    def receive(
        self,
        format: PCM | Opus = DEFAULT_PCM,
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        buffer: float | None = None,
        jitter: float | None = None,
        markers: Literal[False] = False,
        mix: bool = False,
        overflow: Literal["drop_oldest", "drop_newest"] | None = None,
        stall: Literal["wait", "end"] | None = None,
    ) -> Receiver[Frame]: ...
    @overload
    def receive(
        self,
        format: PCM | Opus = DEFAULT_PCM,
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        buffer: float | None = None,
        jitter: float | None = None,
        markers: Literal[True],
        mix: bool = False,
        overflow: Literal["drop_oldest", "drop_newest"] | None = None,
        stall: Literal["wait", "end"] | None = None,
    ) -> Receiver[Frame | SpeechStart | SpeechStop]: ...
    @overload
    def receive(
        self,
        format: PCM | Opus = DEFAULT_PCM,
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        buffer: float | None = None,
        jitter: float | None = None,
        markers: bool,
        mix: bool = False,
        overflow: Literal["drop_oldest", "drop_newest"] | None = None,
        stall: Literal["wait", "end"] | None = None,
    ) -> Receiver[Frame | SpeechStart | SpeechStop]: ...
    def receive(
        self,
        format: ReceiveFormat = DEFAULT_PCM,
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        buffer: float | None = None,
        jitter: float | None = None,
        markers: bool = False,
        mix: bool = False,
        overflow: Literal["drop_oldest", "drop_newest"] | None = None,
        stall: Literal["wait", "end"] | None = None,
    ) -> Receiver[Any]:
        """Open a bounded iterator over the audio this connection receives."""
        self._check_live()
        settings = self._settings
        wanted = None if users is None else frozenset(users)
        excluded = frozenset(exclude)
        buffer = settings.receive_buffer if buffer is None else buffer
        jitter = settings.jitter if jitter is None else jitter
        overflow = settings.overflow if overflow is None else overflow
        stall = settings.receive_stall if stall is None else stall
        pcm = DEFAULT_PCM if isinstance(format, Opus | Packets) else format
        native = self._core().voice_receive(
            self.guild_id,
            kind=(
                "opus"
                if isinstance(format, Opus)
                else "packets"
                if isinstance(format, Packets)
                else "pcm"
            ),
            rate=pcm.rate,
            channels=pcm.channels,
            sample=pcm.sample,
            users=None if wanted is None else sorted(wanted),
            exclude=sorted(excluded),
            include_self=include_self,
            buffer=buffer,
            jitter=jitter,
            markers=markers,
            mix=mix,
            overflow=overflow,
            stall=stall,
        )
        receiver: Receiver[Any] = Receiver(
            self,
            native,
            format,
            users=wanted,
            exclude=excluded,
            include_self=include_self,
            buffer=buffer,
            jitter=jitter,
            markers=markers,
            mix=mix,
            overflow=overflow,
            stall=stall,
        )
        self._receivers.add(receiver)
        self.manager._handles[native.id] = receiver
        if self._deaf:
            self._warn_deafened()
        return receiver

    async def play(
        self,
        source: "Source",
        *,
        volume: float = 1.0,
        buffer: float | None = None,
        stall: Literal["hold", "drop"] | None = None,
        meta: Any = None,
    ) -> layers.Track:
        """Play a source on its own sink, mixed with anything else playing."""
        self._check_live()
        return await layers.play(
            self, source, volume=volume, buffer=buffer, stall=stall, meta=meta
        )

    async def move(self, channel_id: int, *, timeout: float | None = None) -> None:
        """Move to another voice or stage channel and wait until media flows there."""
        self._check_live()
        guild_id, channel = await self.manager._voice_channel(channel_id)
        if guild_id != self.guild_id:
            raise ValueError(f"channel {channel_id} is in another guild")
        self._check_live()
        await self._join(
            channel,
            *self._requested,
            self._settings.timeout if timeout is None else timeout,
        )

    async def edit(
        self, *, self_mute: bool | None = None, self_deaf: bool | None = None
    ) -> None:
        """Change the bot's own mute or deafen in this channel."""
        self._check_live()
        mute, deaf = self._requested
        self._update(
            self._channel_id,
            mute if self_mute is None else self_mute,
            deaf if self_deaf is None else self_deaf,
        )

    async def stage(self, role: StageRole) -> None:
        """Listen, ask to speak, or speak on this stage channel."""
        self._check_live()
        if not self._stage_channel:
            raise ValueError(f"channel {self._channel_id} is not a stage channel")
        await self._request_stage(role)

    async def wait_until(self, state: State) -> None:
        """Wait until the connection reaches `state`; VoiceNotConnected if it ends.

        Bound the wait with asyncio.timeout().
        """
        if not await self._until(lambda: self._state is state):
            raise self._ended()

    def reconnect(self) -> None:
        """Drop the voice WebSocket and resume on a new one."""
        self._check_live()
        self._core().voice_reconnect(self.guild_id)

    async def disconnect(self) -> None:
        """Leave the channel; returns once the connection ended, at most 2 s later."""
        if self._state is State.DISCONNECTED:
            return
        self._core().voice_disconnect(self.guild_id)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(CLOSE_TIMEOUT):
                await self._until(lambda: self._state is State.DISCONNECTED)

    async def _start(self, user_id: int) -> None:
        settings = self._settings
        encoder = settings.encoder
        self.manager._connections[self.guild_id] = self
        try:
            self._core().voice_connect(
                self.guild_id,
                self._channel_id,
                shard_id=SHARD,
                user_id=user_id,
                self_mute=settings.self_mute,
                self_deaf=settings.self_deaf,
                timeout=settings.timeout,
                server_change_delay=settings.server_change_delay,
                bitrate=self._bitrate(encoder),
                application=encoder.application,
                fec=encoder.fec,
                packet_loss=percent(encoder.packet_loss),
                complexity=encoder.complexity,
                speaking=int(settings.speaking_flags),
            )
            self._core().voice_speech_hangover(self.guild_id, settings.speech_hangover)
            self._core().voice_speech_activity(self.guild_id, settings.speech_activity)
        except BaseException:
            del self.manager._connections[self.guild_id]
            raise
        try:
            await self._until_ready()
        except asyncio.CancelledError:
            with contextlib.suppress(RuntimeError):
                self._core().voice_disconnect(self.guild_id)
            raise

    async def _join(
        self, channel: Channel, self_mute: bool, self_deaf: bool, timeout: float
    ) -> bool:
        moving = channel.id != self._channel_id
        if moving or (self_mute, self_deaf) != self._requested:
            self._update(channel.id, self_mute, self_deaf)
        try:
            async with asyncio.timeout(timeout):
                reached = await self._until(
                    lambda: (
                        self._channel_id == channel.id and self._state is State.READY
                    )
                )
        except TimeoutError:
            detail = (
                f"no move to {channel.id}; the channel may be full or the bot lacks "
                "CONNECT"
                if moving
                else f"not ready within {timeout} s"
            )
            raise VoiceConnectError(DisconnectReason.TIMEOUT, None, detail) from None
        if not reached:
            raise self._failure()
        return moving

    async def _until_ready(self) -> None:
        if not await self._until(lambda: self._state is State.READY):
            raise self._failure()

    async def _until(self, done: Callable[[], bool]) -> bool:
        if done():
            return True
        if self._state is State.DISCONNECTED:
            return False
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        watcher = (done, future)
        self._watchers.append(watcher)
        try:
            return await future
        finally:
            self._watchers.remove(watcher)

    def _changed(self) -> None:
        for done, future in list(self._watchers):
            if future.done():
                continue
            if done():
                future.set_result(True)
            elif self._state is State.DISCONNECTED:
                future.set_result(False)

    def _apply(self, delivery: VoiceDelivery) -> None:
        match delivery:
            case ("voice_state", _, status, reason, code, detail):
                self._set_state(status, reason, code, detail)
            case ("voice_moved", _, old, new, by_us):
                self._channel_id = new
                self._members = frozenset()
                for receiver in list(self._receivers):
                    receiver._wake()
                self._emit(
                    Moved(
                        guild_id=self.guild_id,
                        old_channel_id=old,
                        new_channel_id=new,
                        by_us=by_us,
                    )
                )
                self._changed()
                spawn(self.manager._tasks, self._follow(new))
            case ("voice_dave", _, version, epoch, transition):
                self._emit(
                    DaveTransition(
                        guild_id=self.guild_id,
                        protocol_version=version,
                        epoch=epoch,
                        transition_id=transition,
                    )
                )
            case ("voice_self", _, self_mute, self_deaf, mute, suppress):
                self._self_mute, self._self_deaf = self_mute, self_deaf
                self._mute, self._suppress = mute, suppress
            case ("voice_speaking", _, user_id, ssrc, flags):
                self._emit(
                    SpeakingUpdate(
                        guild_id=self.guild_id,
                        user_id=user_id,
                        ssrc=ssrc,
                        flags=SpeakingFlags(flags),
                    )
                )
            case ("voice_clients", _, user_ids):
                joined = [
                    user
                    for user in dict.fromkeys(user_ids)
                    if user not in self._members
                ]
                self._members |= frozenset(joined)
                for user_id in joined:
                    self._emit(UserJoin(guild_id=self.guild_id, user_id=user_id))
            case ("voice_client_left", _, user_id):
                self._members -= {user_id}
                self._emit(UserLeave(guild_id=self.guild_id, user_id=user_id))
            case ("voice_stalled", _, direction, stalled, seconds):
                change = MediaStalled if stalled else MediaResumed
                self._emit(
                    change(
                        guild_id=self.guild_id,
                        direction=Direction(direction),
                        duration=seconds,
                    )
                )
            case ("voice_speech", _, user_id, ssrc, True, age, _):
                at = time.monotonic() - age
                self._speaking[user_id] = at
                self._speech_ssrcs[user_id] = ssrc
                self._emit(
                    SpeechStart(
                        guild_id=self.guild_id, user_id=user_id, ssrc=ssrc, at=at
                    )
                )
            case ("voice_speech", _, user_id, ssrc, False, age, duration):
                self._speaking.pop(user_id, None)
                self._speech_ssrcs.pop(user_id, None)
                self._emit(
                    SpeechStop(
                        guild_id=self.guild_id,
                        user_id=user_id,
                        ssrc=ssrc,
                        at=time.monotonic() - age,
                        duration=duration,
                    )
                )
            case ("voice_deaf", _, deaf):
                if deaf and not self._deaf and self._receivers:
                    self._warn_deafened()
                self._deaf = deaf

    def _set_state(
        self,
        status: str,
        reason: str | None,
        code: int | None,
        detail: str,
    ) -> None:
        if self._state is State.DISCONNECTED:
            return
        old, self._state = self._state, State(status)
        self._reason = None if reason is None else DisconnectReason(reason)
        self._code = code
        self._detail = detail
        if self._state is State.DISCONNECTED:
            if self.manager._connections.get(self.guild_id) is self:
                del self.manager._connections[self.guild_id]
            for sink in list(self._sinks):
                sink._release()
            for receiver in list(self._receivers):
                receiver._release()
            self._stop_speech()
        if self._state is not old:
            self._emit(
                StateChange(
                    guild_id=self.guild_id,
                    old=old,
                    new=self._state,
                    reason=self._reason,
                    code=code,
                    detail=detail,
                )
            )
        self._changed()

    async def _follow(self, channel_id: int) -> None:
        try:
            _, channel = await self.manager._voice_channel(channel_id)
            if self._channel_id != channel_id or self._state is State.DISCONNECTED:
                return
            self._stage_channel = channel.type is ChannelType.GUILD_STAGE_VOICE
            self._channel_bitrate = channel.bitrate
            if self._encoder.bitrate is None:
                self._apply_encoder(self._encoder)
        except Exception:
            log.warning(
                "could not read channel %d to follow its bitrate",
                channel_id,
                exc_info=True,
            )

    async def _request_stage(self, role: StageRole) -> None:
        body: dict[str, Any] = {"channel_id": str(self._channel_id)}
        match role:
            case "listen" | "speak":
                body["suppress"] = role == "listen"
            case "request":
                body["request_to_speak_timestamp"] = datetime.now(UTC).isoformat()
            case _:
                raise ValueError(
                    f"stage must be 'listen', 'request' or 'speak', got {role!r}"
                )
        await self.manager._bot.http.request(
            "PATCH",
            "/guilds/{guild_id}/voice-states/@me",
            json=body,
            guild_id=self.guild_id,
        )

    async def _verification_code(self, user_id: int) -> str:
        self._check_live()
        return await asyncio.to_thread(
            self._core().voice_verification_code, self.guild_id, user_id
        )

    def _update(self, channel_id: int, self_mute: bool, self_deaf: bool) -> None:
        self._core().voice_update(self.guild_id, channel_id, self_mute, self_deaf)
        self._requested = (self_mute, self_deaf)

    def _apply_encoder(self, encoder: EncoderSettings) -> None:
        self._core().voice_encoder(
            self.guild_id,
            bitrate=self._bitrate(encoder),
            application=encoder.application,
            fec=encoder.fec,
            packet_loss=percent(encoder.packet_loss),
            complexity=encoder.complexity,
        )

    def _bitrate(self, encoder: EncoderSettings) -> int:
        return encoder.bitrate or self._channel_bitrate or DEFAULT_BITRATE

    def _audio_started(self) -> None:
        if self._warned or not (self._mute or self._suppress):
            return
        self._warned = True
        if self._mute:
            log.warning(
                "nobody hears the bot in guild %d: it is server muted", self.guild_id
            )
        else:
            log.warning(
                "nobody hears the bot in guild %d: it is suppressed on this stage; "
                "use vc.stage('request')",
                self.guild_id,
            )

    def _warn_deafened(self) -> None:
        log.warning(
            "a moderator deafened the bot in guild %d, so Discord sends it no audio",
            self.guild_id,
        )

    def _stop_speech(self) -> None:
        now = time.monotonic()
        speakers = list(self._speaking.items())
        self._speaking.clear()
        for user_id, started in speakers:
            self._emit(
                SpeechStop(
                    guild_id=self.guild_id,
                    user_id=user_id,
                    ssrc=self._speech_ssrcs.pop(user_id),
                    at=now,
                    duration=now - started,
                )
            )

    def _forget(self, sink: Sink) -> None:
        self._sinks.discard(sink)
        self.manager._handles.pop(sink._handle, None)

    def _forget_receiver(self, receiver: Receiver[Any]) -> None:
        self._receivers.discard(receiver)
        self.manager._handles.pop(receiver._handle, None)

    def _emit(self, event: VoiceEvent) -> None:
        self.manager._bot._emit(event)

    def _core(self) -> VoiceCore:
        return self.manager._core()

    def _check_live(self) -> None:
        if self._state is State.DISCONNECTED:
            raise self._ended()

    def _ended(self) -> VoiceNotConnected:
        return VoiceNotConnected(
            f"the voice connection in guild {self.guild_id} has ended"
        )

    def _failure(self) -> VoiceConnectError:
        return VoiceConnectError(
            self._reason or DisconnectReason.CLOSED, self._code, self._detail
        )


class ContextVoice:
    """Voice for the guild and the user of an interaction."""

    def __init__(
        self, manager: VoiceManager, guild_id: int | None, user_id: int
    ) -> None:
        self._manager = manager
        self._guild_id = guild_id
        self._user_id = user_id

    @property
    def current(self) -> VoiceConnection | None:
        """The bot's connection in this guild, or None."""
        return None if self._guild_id is None else self._manager.get(self._guild_id)

    async def connect(self, **options: Unpack[ConnectOptions]) -> VoiceConnection:
        """Connect to the voice channel of the user who ran the command."""
        if self._guild_id is None:
            raise NotInVoice("voice needs a guild; this interaction came from a DM")
        try:
            state = await self._manager._bot.http.request(
                "GET",
                "/guilds/{guild_id}/voice-states/{user_id}",
                guild_id=self._guild_id,
                user_id=self._user_id,
            )
        except NotFound as error:
            if error.code != UNKNOWN_VOICE_STATE:
                raise
            state = {}
        channel_id = state.get("channel_id")
        if channel_id is None:
            raise NotInVoice(
                f"user {self._user_id} is not in a voice channel of guild "
                f"{self._guild_id}"
            )
        return await self._manager.connect(int(channel_id), **options)


def versions() -> dict[str, str]:
    """Versions of bostie, libopus, davey and the DAVE protocol in this build."""
    return _native.versions()
