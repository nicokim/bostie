import asyncio
import json
import threading
from collections import deque
from collections.abc import Buffer, Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import msgspec
from fakediscord import settle

from bostie.cache import Cache
from bostie.enums import Intents
from bostie.errors import (
    AlreadyConnected,
    ReceiverClosed,
    SinkClosed,
    StateError,
    VoiceNotConnected,
)
from bostie.events import Event
from bostie.gateway import GatewayClient
from bostie.http import HTTPClient
from bostie.models import AllowedMentions, User
from bostie.rest import REST
from bostie.voice.connection import VoiceConnection, VoiceDelivery, VoiceManager
from bostie.voice.events import VoiceEvent
from bostie.voice.formats import Defaults, OpusApplication, Sample, SinkStall

if TYPE_CHECKING:
    from bostie._bridge import Bridge
    from bostie._native import ReceivedItem

BOT_ID = 1000
USER_ID = 2000
GUILD_ID = 1
OTHER_GUILD_ID = 2
VOICE = 10
OTHER_VOICE = 11
STAGE = 12
TEXT = 13
ELSEWHERE = 20
NO_BITRATE = 21
SAMPLE_RATE = 48_000
DEFAULTS = Defaults()

CHANNELS: dict[int, dict[str, Any]] = {
    VOICE: {"id": str(VOICE), "type": 2, "guild_id": str(GUILD_ID), "bitrate": 96_000},
    OTHER_VOICE: {
        "id": str(OTHER_VOICE),
        "type": 2,
        "guild_id": str(GUILD_ID),
        "bitrate": 32_000,
    },
    STAGE: {"id": str(STAGE), "type": 13, "guild_id": str(GUILD_ID), "bitrate": 64_000},
    TEXT: {"id": str(TEXT), "type": 0, "guild_id": str(GUILD_ID)},
    ELSEWHERE: {"id": str(ELSEWHERE), "type": 2, "guild_id": str(OTHER_GUILD_ID)},
    NO_BITRATE: {"id": str(NO_BITRATE), "type": 2, "guild_id": str(OTHER_GUILD_ID)},
}

type Call = tuple[str, tuple[Any, ...], dict[str, Any]]


@dataclass
class Request:
    method: str
    path: str
    json: Any


class FakeSink:
    def __init__(
        self,
        core: "FakeCore",
        id: int,
        *,
        kind: Literal["pcm", "opus"],
        rate: int,
        channels: int,
        sample: str,
        buffer: float,
        volume: float,
        stall: str,
    ) -> None:
        self._id = id
        self.core = core
        self.kind = kind
        self.rate = rate
        self.channels = channels
        self.sample = sample
        self.buffer = buffer
        self.volume = volume
        self.stall = stall
        self.bytes_per_second = rate * channels * (2 if sample == "s16le" else 4)
        self.writes: list[bytes] = []
        self.written = 0.0
        self.consumed = 0.0
        self.armed = False
        self.markers: list[tuple[int, float]] = []
        self.next_marker = 1
        self.flushes = 0
        self.clears: list[int | None] = []
        self.since: list[int | None] = []
        self.closed = False
        self.failure: Exception | None = None
        self.counters = (0, 0, 0, 0, 0)

    @property
    def id(self) -> int:
        return self._id

    def write(self, data: Buffer) -> bool:
        self._check()
        if self.queued() >= self.buffer:
            self.armed = True
            return False
        chunk = bytes(data)
        self.writes.append(chunk)
        self.written += (
            0.02 if self.kind == "opus" else len(chunk) / self.bytes_per_second
        )
        return True

    def mark(self) -> tuple[int, int]:
        self._check()
        marker = self.next_marker
        self.next_marker += 1
        self.markers.append((marker, self.written))
        return marker, round(self.written * SAMPLE_RATE)

    def flush(self) -> tuple[int, int]:
        self._check()
        self.flushes += 1
        return self.mark()

    def clear(self, until: int | None = None, since: int | None = None) -> None:
        self._check()
        self.clears.append(until)
        self.since.append(since)
        end = next(
            (position for marker, position in self.markers if marker == until),
            self.written,
        )
        self.consumed = max(self.consumed, end)
        self._settle(played=False)

    def set_volume(self, volume: float) -> None:
        self._check()
        self.volume = volume

    def queued(self) -> float:
        self._check()
        return self.written - self.consumed

    def passthrough(self) -> bool:
        self._check()
        return False

    def stats(self) -> tuple[int, int, int, int, int, int]:
        self._check()
        sent, underruns, held_ticks, dropped, passthrough = self.counters
        consumed = round(self.consumed * SAMPLE_RATE)
        return sent, consumed, underruns, held_ticks, dropped, passthrough

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.consumed = self.written
            self._settle(played=False)

    def pull(self, seconds: float) -> None:
        self.consumed = min(self.written, self.consumed + seconds)
        self._settle(played=True)
        if self.armed and self.written - self.consumed <= self.buffer / 2:
            self.armed = False
            self.core.deliver(("voice_wake", self._id))

    def _settle(self, *, played: bool) -> None:
        for marker, position in list(self.markers):
            if position <= self.consumed + 1e-9:
                self.markers.remove((marker, position))
                self.core.deliver(("voice_marker", self._id, marker, played))

    def _check(self) -> None:
        if self.failure is not None:
            raise self.failure
        if self.closed:
            raise SinkClosed(f"sink {self._id} is closed")


class FakeSource:
    def __init__(self, core: "FakeCore", id: int, arguments: tuple[Any, ...]) -> None:
        self._id = id
        self.core = core
        self.arguments = arguments
        self.probe = core.probe
        self.failure: str | None = None
        self.items: deque[list[bytes] | None] = deque()
        self.seeks: list[float] = []
        self.closed = False

    @property
    def id(self) -> int:
        return self._id

    def info(self) -> tuple[Literal["pcm", "opus"], float | None, bool] | None:
        self._check()
        return self.probe

    def tags(self) -> tuple[str | None, str | None, str | None]:
        self._check()
        return self.core.tags

    def read(self) -> list[bytes] | None:
        self._check()
        if not self.items:
            return []
        if self.items[0] is None:
            return None
        return self.items.popleft()

    def error(self) -> str | None:
        self._check()
        return self.failure

    def seek(self, position: float) -> None:
        self._check()
        self.seeks.append(position)

    def close(self) -> None:
        self.closed = True

    def feed(self, *items: list[bytes] | None) -> None:
        self.items.extend(items)
        self.core.deliver(("voice_wake", self._id))

    def _check(self) -> None:
        if self.closed:
            raise StateError(f"source {self._id} is closed")


class FakeReceiver:
    def __init__(self, core: "FakeCore", id: int) -> None:
        self._id = id
        self.core = core
        self.items: deque[ReceivedItem] = deque()
        self.armed = False
        self.ended: Literal["disconnected", "moved"] | None = None
        self.lag = 0.0
        self.clears = 0
        self.counters = (0, 0, 0)
        self.marks = 0
        self.closed = False

    @property
    def id(self) -> int:
        return self._id

    def read(self, limit: int) -> "list[ReceivedItem] | None":
        self._check()
        if not self.items:
            self.armed = self.ended is None
            return None if self.ended is not None else []
        return [self.items.popleft() for _ in range(min(limit, len(self.items)))]

    def queued(self) -> float:
        self._check()
        return self.lag

    def clear(self) -> None:
        self._check()
        self.items.clear()
        self.clears += 1

    def mark(self) -> int:
        self._check()
        self.marks += 1
        return self.marks

    def stats(self) -> tuple[int, int, int]:
        self._check()
        return self.counters

    def closed_reason(self) -> Literal["closed", "disconnected", "moved"] | None:
        return "closed" if self.closed else self.ended

    def close(self) -> None:
        self.closed = True
        self.items.clear()

    def feed(self, *items: "ReceivedItem") -> None:
        self.items.extend(items)
        if self.armed:
            self.armed = False
            self.core.deliver(("voice_wake", self._id))

    def end(self, reason: Literal["disconnected", "moved"]) -> None:
        self.ended = reason

    def _check(self) -> None:
        if self.closed:
            raise ReceiverClosed(f"receiver {self._id} is closed")


class FakeCore:
    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.sent: list[Any] = []
        self.sinks: list[FakeSink] = []
        self.sources: list[FakeSource] = []
        self.receivers: list[FakeReceiver] = []
        self.ssrc_map: dict[int, int] = {}
        self.handle: Callable[[VoiceDelivery], None] | None = None
        self.auto_disconnect = True
        self.applies_self = True
        self.probe: tuple[Literal["pcm", "opus"], float | None, bool] | None = (
            "pcm",
            3.0,
            True,
        )
        self.latency: tuple[float | None, float | None] = (0.01, 0.02)
        self.dave_view: tuple[int, int | None, str | None] = (1, 4, "11111 22222")
        self.live: set[int] = set()
        self.tags: tuple[str | None, str | None, str | None] = (None, None, None)
        self._next_handle = 100

    def deliver(self, delivery: VoiceDelivery) -> None:
        self.track(delivery)
        handle = self.handle
        if handle is not None:
            asyncio.get_running_loop().call_soon(handle, delivery)

    def track(self, delivery: VoiceDelivery) -> None:
        if delivery[0] == "voice_state" and delivery[2] == "disconnected":
            self.live.discard(delivery[1])

    def gateway_send(self, shard_id: int, payload: bytes) -> None:
        self.sent.append(json.loads(payload))

    def voice_connect(self, guild_id: int, channel_id: int, **options: Any) -> None:
        if guild_id in self.live:
            raise AlreadyConnected(f"guild {guild_id} already has a voice connection")
        self.calls.append(("voice_connect", (guild_id, channel_id), options))
        self.live.add(guild_id)
        self.sent.append(
            voice_state(
                channel_id,
                self_mute=options["self_mute"],
                self_deaf=options["self_deaf"],
                guild_id=guild_id,
            )
        )

    def voice_update(
        self, guild_id: int, channel_id: int, self_mute: bool, self_deaf: bool
    ) -> None:
        self._record("voice_update", guild_id, channel_id, self_mute, self_deaf)
        self.sent.append(
            voice_state(
                channel_id, self_mute=self_mute, self_deaf=self_deaf, guild_id=guild_id
            )
        )
        if self.applies_self:
            self.deliver(("voice_self", guild_id, self_mute, self_deaf, False, False))

    def voice_disconnect(self, guild_id: int) -> None:
        self.calls.append(("voice_disconnect", (guild_id,), {}))
        if guild_id not in self.live:
            return
        self.sent.append(voice_state(None, guild_id=guild_id))
        if self.auto_disconnect:
            self.deliver(
                ("voice_state", guild_id, "disconnected", "requested", None, "")
            )

    def voice_reconnect(self, guild_id: int) -> None:
        self._record("voice_reconnect", guild_id)
        self.deliver(("voice_state", guild_id, "resuming", None, None, ""))
        self.deliver(("voice_state", guild_id, "ready", None, None, ""))

    def voice_encoder(
        self,
        guild_id: int,
        *,
        bitrate: int,
        application: OpusApplication,
        fec: bool,
        packet_loss: int,
        complexity: int,
        bandwidth: str,
        signal: str,
    ) -> None:
        self._record(
            "voice_encoder",
            guild_id,
            bitrate=bitrate,
            application=application,
            fec=fec,
            packet_loss=packet_loss,
            complexity=complexity,
            bandwidth=bandwidth,
            signal=signal,
        )

    def voice_speaking(self, guild_id: int, flags: int) -> None:
        self._record("voice_speaking", guild_id, flags)

    def voice_latency(self, guild_id: int) -> tuple[float | None, float | None]:
        self._record("voice_latency", guild_id)
        return self.latency

    def voice_dave(self, guild_id: int) -> tuple[int, int | None, str | None]:
        self._record("voice_dave", guild_id)
        return self.dave_view

    def voice_verification_code(self, guild_id: int, user_id: int) -> str:
        self._record("voice_verification_code", guild_id, user_id)
        return f"code for {user_id} on {threading.current_thread().name}"

    def voice_sink(
        self,
        guild_id: int,
        *,
        kind: Literal["pcm", "opus"],
        rate: int,
        channels: int,
        sample: Sample,
        buffer: float,
        volume: float,
        stall: SinkStall,
        after: int | None,
        crossfade: float,
    ) -> FakeSink:
        self._record(
            "voice_sink",
            guild_id,
            kind=kind,
            rate=rate,
            channels=channels,
            sample=sample,
            buffer=buffer,
            volume=volume,
            stall=stall,
            after=after,
            crossfade=crossfade,
        )
        sink = FakeSink(
            self,
            self._handle_id(),
            kind=kind,
            rate=rate,
            channels=channels,
            sample=sample,
            buffer=buffer,
            volume=volume,
            stall=stall,
        )
        self.sinks.append(sink)
        return sink

    def voice_file(self, path: str) -> FakeSource:
        return self._source("voice_file", path)

    def voice_bytes(self, data: bytes, hint: str | None) -> FakeSource:
        return self._source("voice_bytes", data, hint)

    def voice_url(self, url: str, headers: list[tuple[str, str]]) -> FakeSource:
        return self._source("voice_url", url, headers)

    def voice_receive(self, guild_id: int, **options: Any) -> FakeReceiver:
        self._record("voice_receive", guild_id, **options)
        receiver = FakeReceiver(self, self._handle_id())
        self.receivers.append(receiver)
        return receiver

    def voice_speech_hangover(self, guild_id: int, hangover: float) -> None:
        self._record("voice_speech_hangover", guild_id, hangover)

    def voice_server_change_delay(self, guild_id: int, delay: float) -> None:
        self._record("voice_server_change_delay", guild_id, delay)

    def voice_speech_activity(self, guild_id: int, enabled: bool) -> None:
        self._record("voice_speech_activity", guild_id, enabled)

    def voice_ssrcs(self, guild_id: int) -> dict[int, int]:
        self._record("voice_ssrcs", guild_id)
        return dict(self.ssrc_map)

    def _source(self, name: str, *arguments: Any) -> FakeSource:
        self.calls.append((name, arguments, {}))
        source = FakeSource(self, self._handle_id(), arguments)
        self.sources.append(source)
        return source

    def _handle_id(self) -> int:
        self._next_handle += 1
        return self._next_handle

    def _record(self, name: str, guild_id: int, *args: Any, **kwargs: Any) -> None:
        if guild_id not in self.live:
            raise VoiceNotConnected(f"no voice connection in guild {guild_id}")
        self.calls.append((name, (guild_id, *args), kwargs))


class FakeBridge:
    def __init__(self, core: FakeCore) -> None:
        self.core = core
        self.requests: list[Request] = []
        self.answers: dict[tuple[str, str], tuple[int, Any]] = {}

    async def request(
        self,
        method: str,
        route: str,
        params: dict[str, str],
        query: list[tuple[str, str]],
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        reason: str | None = None,
    ) -> tuple[int, bytes]:
        path = route.format(**params)
        self.requests.append(
            Request(method, path, None if body is None else json.loads(body))
        )
        status, payload = self.answers.get((method, path)) or self._default(path)
        return status, b"" if payload is None else msgspec.json.encode(payload)

    def _default(self, path: str) -> tuple[int, Any]:
        match path.split("/"):
            case ["", "channels", channel_id] if int(channel_id) in CHANNELS:
                return 200, CHANNELS[int(channel_id)]
            case ["", "guilds", _, "voice-states", "@me"]:
                return 204, None
            case _:
                return 404, {"code": 10003, "message": "Unknown Channel"}


class FakeBot:
    def __init__(
        self,
        *,
        intents: Intents = Intents.DEFAULT,
        defaults: Defaults = DEFAULTS,
        running: bool = True,
    ) -> None:
        self.core = FakeCore()
        self.api = FakeBridge(self.core)
        self._bridge: FakeBridge | None = self.api if running else None
        self.user: User | None = (
            User(
                id=BOT_ID,
                username="bostie",
                discriminator="0",
                global_name=None,
                avatar=None,
                bot=True,
            )
            if running
            else None
        )
        self.cache = Cache()
        self.http = HTTPClient(cast("Bridge", self.api))
        self.rest = REST(self.http, AllowedMentions(), self.cache)
        self.gateway = GatewayClient(
            cast("Bridge", self.api),
            "token",
            intents=intents,
            shard_count=1,
            shard_ids=[0],
        )
        self._intents = intents
        self.events: list[VoiceEvent] = []
        self.voice = VoiceManager(self, defaults)
        self.core.handle = self.voice._handle
        self._waiters: list[
            tuple[type[Event], Callable[[Any], bool] | None, asyncio.Future[Event]]
        ] = []

    def _emit(self, event: VoiceEvent) -> None:
        self.events.append(event)

    async def wait_for[E: Event](
        self, event: type[E], *, check: Callable[[E], bool] | None = None
    ) -> E:
        future: asyncio.Future[Event] = asyncio.get_running_loop().create_future()
        waiter = (
            cast("type[Event]", event),
            cast("Callable[[Any], bool] | None", check),
            future,
        )
        self._waiters.append(waiter)
        try:
            return cast("E", await future)
        finally:
            self._waiters.remove(waiter)

    def dispatch(self, event: Event) -> None:
        for kind, check, future in list(self._waiters):
            if (
                isinstance(event, kind)
                and not future.done()
                and (check is None or check(event))
            ):
                future.set_result(event)

    def deliver(self, delivery: VoiceDelivery) -> None:
        self.core.track(delivery)
        self.voice._handle(delivery)

    def events_of[V: VoiceEvent](self, kind: type[V]) -> list[V]:
        return [event for event in self.events if isinstance(event, kind)]

    def calls(self, name: str) -> list[Call]:
        return [call for call in self.core.calls if call[0] == name]


def voice_state(
    channel_id: int | None,
    *,
    self_mute: bool = False,
    self_deaf: bool = False,
    guild_id: int = GUILD_ID,
) -> dict[str, Any]:
    return {
        "op": 4,
        "d": {
            "guild_id": str(guild_id),
            "channel_id": None if channel_id is None else str(channel_id),
            "self_mute": self_mute,
            "self_deaf": self_deaf,
        },
    }


def rest_voice_state(
    channel_id: int | None, user_id: int = USER_ID, guild_id: int = GUILD_ID
) -> dict[str, Any]:
    return {
        "guild_id": str(guild_id),
        "channel_id": None if channel_id is None else str(channel_id),
        "user_id": str(user_id),
        "session_id": "abc",
        "deaf": False,
        "mute": False,
        "self_deaf": False,
        "self_mute": False,
        "self_video": False,
        "suppress": False,
        "request_to_speak_timestamp": None,
    }


def ready(guild_id: int = GUILD_ID) -> VoiceDelivery:
    return ("voice_state", guild_id, "ready", None, None, "")


def disconnected(
    reason: Literal["requested", "removed", "timeout", "authentication_failed"],
    code: int | None = None,
    detail: str = "",
    guild_id: int = GUILD_ID,
) -> VoiceDelivery:
    return ("voice_state", guild_id, "disconnected", reason, code, detail)


async def connected(
    bot: FakeBot, channel_id: int = VOICE, guild_id: int = GUILD_ID, **options: Any
) -> VoiceConnection:
    task = asyncio.ensure_future(bot.voice.connect(channel_id, **options))
    await settle()
    bot.deliver(ready(guild_id))
    return await task


def run(
    scenario: Callable[[FakeBot], Coroutine[Any, Any, None]], **options: Any
) -> FakeBot:
    bot = FakeBot(**options)

    async def main() -> None:
        async with asyncio.timeout(10):
            await scenario(bot)

    asyncio.run(main())
    return bot
