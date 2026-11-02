from collections.abc import Buffer
from typing import Final, Literal, final

__all__ = [
    "CLOSED_REASONS",
    "DECODE_RATES",
    "DIRECTIONS",
    "DISCONNECT_REASONS",
    "LOGGERS",
    "MAX_BANDS",
    "MAX_BAND_FREQUENCY",
    "MAX_BITRATE",
    "MAX_BUFFER",
    "MAX_COMPLEXITY",
    "MAX_GAIN",
    "MAX_JITTER",
    "MAX_RATE",
    "MAX_RESAMPLER_BLOCK",
    "MAX_SPEECH_HANGOVER",
    "MAX_SPEED",
    "MIN_BAND_FREQUENCY",
    "MIN_BITRATE",
    "MIN_BUFFER",
    "MIN_RATE",
    "MIN_SPEECH_HANGOVER",
    "MIN_SPEED",
    "OPUS_APPLICATIONS",
    "OPUS_BANDWIDTHS",
    "OPUS_SIGNALS",
    "OVERFLOWS",
    "RECEIVE_KINDS",
    "RECEIVE_STALLS",
    "SAMPLES",
    "SAMPLE_RATE",
    "SINK_KINDS",
    "SINK_STALLS",
    "VOICE_STATES",
    "Core",
    "OpusEncoder",
    "Receiver",
    "Sink",
    "Source",
    "log_levels",
    "verify_signature",
    "versions",
    "voice_message",
]

SAMPLE_RATE: Final[int]
MIN_RATE: Final[int]
MAX_RATE: Final[int]
MAX_RESAMPLER_BLOCK: Final[int]
DECODE_RATES: Final[list[int]]
MIN_BITRATE: Final[int]
MAX_BITRATE: Final[int]
MAX_COMPLEXITY: Final[int]
MIN_BUFFER: Final[float]
MAX_BUFFER: Final[float]
MIN_SPEECH_HANGOVER: Final[float]
MAX_SPEECH_HANGOVER: Final[float]
MAX_JITTER: Final[float]
MAX_BANDS: Final[int]
MIN_BAND_FREQUENCY: Final[float]
MAX_BAND_FREQUENCY: Final[float]
MAX_GAIN: Final[float]
MIN_SPEED: Final[float]
MAX_SPEED: Final[float]
OPUS_APPLICATIONS: Final[list[str]]
OPUS_BANDWIDTHS: Final[list[str]]
OPUS_SIGNALS: Final[list[str]]
SAMPLES: Final[list[str]]
SINK_KINDS: Final[list[str]]
RECEIVE_KINDS: Final[list[str]]
SINK_STALLS: Final[list[str]]
RECEIVE_STALLS: Final[list[str]]
OVERFLOWS: Final[list[str]]
VOICE_STATES: Final[list[str]]
DISCONNECT_REASONS: Final[list[str]]
DIRECTIONS: Final[list[str]]
CLOSED_REASONS: Final[list[str]]
LOGGERS: Final[list[str]]

type FailureKind = Literal[
    "transport", "invalid_token", "no_token", "request_budget", "bug"
]
type VoiceStateName = Literal[
    "connecting", "ready", "resuming", "reconnecting", "disconnected"
]
type DisconnectReasonName = Literal[
    "requested",
    "removed",
    "call_terminated",
    "rate_limited",
    "timeout",
    "session_invalid",
    "authentication_failed",
    "dave_required",
    "dave_failed",
    "protocol_error",
    "closed",
]
type ApplicationName = Literal["audio", "voip", "lowdelay"]
type BandwidthName = Literal["auto", "narrow", "medium", "wide", "superwide", "full"]
type SignalName = Literal["auto", "voice", "music"]
type SampleName = Literal["s16le", "f32le"]
type StallName = Literal["hold", "drop"]
type ReceiveKindName = Literal["pcm", "opus", "packets"]
type OverflowName = Literal["drop_oldest", "drop_newest"]
type ReceiveStallName = Literal["wait", "end"]
type ClosedReasonName = Literal["closed", "disconnected", "moved", "reconnecting"]

type ResponseDelivery = tuple[Literal["response"], int, int, bytes]
type FailureDelivery = tuple[Literal["failure"], int, FailureKind, str]
type DispatchDelivery = tuple[Literal["dispatch"], int, int, str, bytes]
type DisconnectedDelivery = tuple[Literal["disconnected"], int, int | None, str, bool]
type StoppedDelivery = tuple[
    Literal["stopped"], int, int | None, str, tuple[str, int, str] | None
]
type BackpressureDelivery = tuple[Literal["backpressure"], int, int]
type InvalidRequestsDelivery = tuple[Literal["invalid_requests"], int]
type IdentifyDelivery = tuple[Literal["identify"], int, int]
type VoiceStateDelivery = tuple[
    Literal["voice_state"],
    int,
    VoiceStateName,
    DisconnectReasonName | None,
    int | None,
    str,
]
type VoiceMovedDelivery = tuple[Literal["voice_moved"], int, int, int, bool]
type VoiceDaveDelivery = tuple[Literal["voice_dave"], int, int, int | None, int]
type VoiceSelfDelivery = tuple[Literal["voice_self"], int, bool, bool, bool, bool]
type VoiceSpeakingDelivery = tuple[Literal["voice_speaking"], int, int, int, int]
type VoiceClientsDelivery = tuple[Literal["voice_clients"], int, list[int]]
type VoiceClientLeftDelivery = tuple[Literal["voice_client_left"], int, int]
type VoiceStalledDelivery = tuple[
    Literal["voice_stalled"], int, Literal["send", "receive"], bool, float
]
type VoiceWakeDelivery = tuple[Literal["voice_wake"], int]
type VoiceMarkerDelivery = tuple[Literal["voice_marker"], int, int, bool]
type VoiceSpeechDelivery = tuple[
    Literal["voice_speech"], int, int, int, bool, float, float
]
type VoiceDeafDelivery = tuple[Literal["voice_deaf"], int, bool]
type ReportItem = tuple[int, int | None, int, int, int, int, int, int]
type RtcpPacketItem = tuple[int, int, int, int | None, list[ReportItem], bytes]
type VoiceRtcpDelivery = tuple[Literal["voice_rtcp"], int, list[RtcpPacketItem]]
type LogDelivery = tuple[Literal["log"], int, str, str]
type Delivery = (
    ResponseDelivery
    | FailureDelivery
    | DispatchDelivery
    | DisconnectedDelivery
    | StoppedDelivery
    | BackpressureDelivery
    | InvalidRequestsDelivery
    | IdentifyDelivery
    | VoiceStateDelivery
    | VoiceMovedDelivery
    | VoiceDaveDelivery
    | VoiceSelfDelivery
    | VoiceSpeakingDelivery
    | VoiceClientsDelivery
    | VoiceClientLeftDelivery
    | VoiceStalledDelivery
    | VoiceWakeDelivery
    | VoiceMarkerDelivery
    | VoiceSpeechDelivery
    | VoiceDeafDelivery
    | VoiceRtcpDelivery
    | LogDelivery
)

type FrameItem = tuple[
    Literal["frame"],
    int | None,
    int | None,
    int | None,
    int,
    int,
    float,
    bytes,
    bool,
    float | None,
    float,
]
type PacketItem = tuple[
    Literal["packet"], int, int, int, int, float, bytes, list[tuple[int, bytes]], float
]
type SpeechItem = tuple[
    Literal["speech"], int, int, bool, int | None, float, float, float
]
type LeaveItem = tuple[Literal["leave"], int, int | None, float]
type ReceivedItem = FrameItem | PacketItem | SpeechItem | LeaveItem

@final
class Core:
    @staticmethod
    def create(
        token: str | None,
        api_url: str,
        *,
        global_limit: int,
        max_rate_limit_wait: float,
        retries: int,
        connect_timeout: float,
        read_timeout: float,
        bearer: bool = False,
    ) -> tuple[Core, int]: ...
    def request(
        self,
        method: str,
        route: str,
        params: dict[str, str],
        query: list[tuple[str, str]],
        body: bytes | None = None,
        content_type: str | None = None,
        reason: str | None = None,
    ) -> int: ...
    def gateway_identify_limit(
        self, remaining: int, total: int, reset_after: float, max_concurrency: int
    ) -> None: ...
    def gateway_gate_identifies(self) -> None: ...
    def gateway_approve_identify(self, shard_id: int, request: int) -> None: ...
    def gateway_start(
        self,
        shard_id: int,
        url: str,
        identify: bytes,
        session: tuple[str, int, str] | None = None,
    ) -> None: ...
    def gateway_identify(self, shard_id: int, identify: bytes) -> None: ...
    def gateway_send(self, shard_id: int, payload: bytes) -> None: ...
    def gateway_filter(self, names: list[str] | None) -> None: ...
    def gateway_reconnect(self, shard_id: int) -> None: ...
    def gateway_close(self, shard_id: int) -> None: ...
    def gateway_suspend(self, shard_id: int) -> None: ...
    def gateway_latency(self, shard_id: int) -> float | None: ...
    def cancel(self, ticket: int) -> None: ...
    def drain(self) -> list[Delivery]: ...
    def close(self) -> None: ...
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
        application: ApplicationName,
        fec: bool,
        packet_loss: int,
        complexity: int,
        speaking: int,
        bandwidth: BandwidthName,
        signal: SignalName,
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
        application: ApplicationName,
        fec: bool,
        packet_loss: int,
        complexity: int,
        bandwidth: BandwidthName,
        signal: SignalName,
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
        sample: SampleName,
        buffer: float,
        volume: float,
        stall: StallName,
        after: int | None = None,
        crossfade: float = 0.0,
    ) -> Sink: ...
    def voice_file(self, path: str) -> Source: ...
    def voice_bytes(self, data: bytes, hint: str | None) -> Source: ...
    def voice_url(self, url: str, headers: list[tuple[str, str]]) -> Source: ...
    def voice_receive(
        self,
        guild_id: int,
        *,
        kind: ReceiveKindName,
        rate: int,
        channels: int,
        sample: SampleName,
        users: list[int] | None,
        exclude: list[int],
        include_self: bool,
        buffer: float,
        jitter: float,
        markers: bool,
        mix: bool,
        overflow: OverflowName,
        stall: ReceiveStallName,
    ) -> Receiver: ...
    def voice_speech_hangover(self, guild_id: int, hangover: float) -> None: ...
    def voice_server_change_delay(self, guild_id: int, delay: float) -> None: ...
    def voice_speech_activity(self, guild_id: int, enabled: bool) -> None: ...
    def voice_ssrcs(self, guild_id: int) -> dict[int, int]: ...

@final
class Sink:
    @property
    def id(self) -> int: ...
    def write(self, data: Buffer) -> bool: ...
    def mark(self) -> tuple[int, int]: ...
    def flush(self) -> tuple[int, int]: ...
    def clear(self, until: int | None = None, since: int | None = None) -> None: ...
    def set_volume(self, volume: float) -> None: ...
    def set_filters(
        self, bands: list[tuple[float, float]], bass: float, speed: float, pitch: float
    ) -> None: ...
    def queued(self) -> float: ...
    def passthrough(self) -> bool: ...
    def stats(self) -> tuple[int, int, int, int, int, int]: ...
    def close(self) -> None: ...

@final
class Source:
    @property
    def id(self) -> int: ...
    def info(self) -> tuple[Literal["pcm", "opus"], float | None, bool] | None: ...
    def tags(self) -> tuple[str | None, str | None, str | None]: ...
    def read(self) -> list[bytes] | None: ...
    def error(self) -> str | None: ...
    def seek(self, position: float) -> None: ...
    def close(self) -> None: ...

@final
class Receiver:
    @property
    def id(self) -> int: ...
    def read(self, limit: int) -> list[ReceivedItem] | None: ...
    def queued(self) -> float: ...
    def clear(self) -> None: ...
    def mark(self) -> int: ...
    def stats(self) -> tuple[int, int, int]: ...
    def closed_reason(self) -> ClosedReasonName | None: ...
    def close(self) -> None: ...

@final
class OpusEncoder:
    def __new__(
        cls,
        *,
        rate: int,
        channels: int,
        sample: SampleName,
        bitrate: int,
        application: ApplicationName,
        fec: bool,
        packet_loss: int,
        complexity: int,
        bandwidth: BandwidthName,
        signal: SignalName,
    ) -> OpusEncoder: ...
    def encode(self, data: Buffer) -> list[bytes]: ...
    def flush(self) -> list[bytes]: ...

def versions() -> dict[str, str]: ...
def log_levels(levels: list[int]) -> None: ...
def verify_signature(
    public_key: str, signature: str, timestamp: str, body: bytes
) -> bool: ...
def voice_message(data: bytes, hint: str | None = None) -> tuple[float, bytes]: ...
