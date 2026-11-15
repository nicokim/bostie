import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, get_args

from bostie import _native
from bostie.enums import SpeakingFlags

_SAMPLE_RATE = _native.SAMPLE_RATE
_MIN_RATE = _native.MIN_RATE
_MAX_RATE = _native.MAX_RATE
_MAX_RESAMPLER_BLOCK = _native.MAX_RESAMPLER_BLOCK
_MIN_BITRATE = _native.MIN_BITRATE
_MAX_BITRATE = _native.MAX_BITRATE
_MAX_COMPLEXITY = _native.MAX_COMPLEXITY
_MIN_BUFFER = _native.MIN_BUFFER
_MAX_BUFFER = _native.MAX_BUFFER
_MIN_SPEECH_HANGOVER = _native.MIN_SPEECH_HANGOVER
_MAX_SPEECH_HANGOVER = _native.MAX_SPEECH_HANGOVER
_MIN_STALL_AFTER = _native.MIN_STALL_AFTER
_MAX_JITTER = _native.MAX_JITTER
_MAX_BANDS = _native.MAX_BANDS
_MIN_BAND_FREQUENCY = _native.MIN_BAND_FREQUENCY
_MAX_BAND_FREQUENCY = _native.MAX_BAND_FREQUENCY
_MAX_GAIN = _native.MAX_GAIN
_MIN_SPEED = _native.MIN_SPEED
_MAX_SPEED = _native.MAX_SPEED
DECODE_RATES = frozenset(_native.DECODE_RATES)

type Overflow = Literal["drop_oldest", "drop_newest"]
type ReceiveStall = Literal["wait", "end"]
type SinkStall = Literal["hold", "drop"]
type OpusApplication = Literal["audio", "voip", "lowdelay"]
type OpusBandwidth = Literal["auto", "narrow", "medium", "wide", "superwide", "full"]
type OpusSignal = Literal["auto", "voice", "music"]
type Sample = Literal["s16le", "f32le"]
type StageRole = Literal["listen", "request", "speak"]

_SAMPLE_BYTES: dict[Sample, int] = {"s16le": 2, "f32le": 4}
_STAGE_ROLES: tuple[StageRole, ...] = get_args(StageRole.__value__)


def _check_choice(name: str, value: object, allowed: Sequence[object]) -> None:
    if value not in allowed:
        *most, last = map(repr, allowed)
        raise ValueError(f"{name} must be {', '.join(most)} or {last}, got {value!r}")


def _check_crossfade(seconds: float) -> float:
    if not 0.0 <= seconds <= _MAX_BUFFER:
        raise ValueError(
            f"crossfade must be from 0 to {_MAX_BUFFER} seconds, got {seconds}"
        )
    return seconds


def _check_stall_after(seconds: float) -> None:
    if not (math.isfinite(seconds) and seconds >= _MIN_STALL_AFTER):
        raise ValueError(
            f"stall_after must be at least {_MIN_STALL_AFTER} seconds, got {seconds}"
        )


@dataclass(frozen=True, slots=True)
class PCM:
    """Raw audio samples, interleaved, little endian."""

    rate: int = _SAMPLE_RATE
    channels: Literal[1, 2] = 2
    sample: Sample = "s16le"

    def __post_init__(self) -> None:
        if not _MIN_RATE <= self.rate <= _MAX_RATE:
            raise ValueError(
                f"sample rate must be from {_MIN_RATE} to {_MAX_RATE} Hz, "
                f"got {self.rate}"
            )
        if _SAMPLE_RATE // math.gcd(self.rate, _SAMPLE_RATE) > _MAX_RESAMPLER_BLOCK:
            raise ValueError(
                f"sample rate {self.rate} Hz does not resample to {_SAMPLE_RATE} Hz "
                f"in 20 ms blocks: {_SAMPLE_RATE} / gcd(rate, {_SAMPLE_RATE}) must be "
                f"at most {_MAX_RESAMPLER_BLOCK}"
            )
        _check_choice("channels", self.channels, (1, 2))
        _check_choice("sample", self.sample, _native.SAMPLES)


_DEFAULT_PCM = PCM()


@dataclass(frozen=True, slots=True)
class Opus:
    """One Opus packet per write or item."""


type Format = PCM | Opus


@dataclass(frozen=True, slots=True)
class Packets:
    """Decrypted packets in arrival order, before the jitter buffer."""


type ReceiveFormat = PCM | Opus | Packets


@dataclass(frozen=True, slots=True, kw_only=True)
class EncoderSettings:
    """Opus encoder settings; `bitrate=None` follows the channel.

    `bandwidth` caps the audio band and `signal_type` tunes for voice or music;
    "auto" leaves both to libopus.
    """

    bitrate: int | None = None
    application: OpusApplication = "audio"
    fec: bool = True
    packet_loss: float = 0.1
    complexity: int = 10
    bandwidth: OpusBandwidth = "auto"
    signal_type: OpusSignal = "auto"

    def __post_init__(self) -> None:
        if (
            self.bitrate is not None
            and not _MIN_BITRATE <= self.bitrate <= _MAX_BITRATE
        ):
            raise ValueError(
                f"bitrate must be from {_MIN_BITRATE} to {_MAX_BITRATE} "
                "bits per second, "
                f"got {self.bitrate}"
            )
        _check_choice("application", self.application, _native.OPUS_APPLICATIONS)
        if not 0.0 <= self.packet_loss <= 1.0:
            raise ValueError(
                f"packet loss must be a fraction from 0 to 1, got {self.packet_loss}"
            )
        if not 0 <= self.complexity <= _MAX_COMPLEXITY:
            raise ValueError(
                f"complexity must be from 0 to {_MAX_COMPLEXITY}, got {self.complexity}"
            )
        _check_choice("bandwidth", self.bandwidth, _native.OPUS_BANDWIDTHS)
        _check_choice("signal_type", self.signal_type, _native.OPUS_SIGNALS)


@dataclass(frozen=True, slots=True, kw_only=True)
class Filters:
    """Effects applied in Rust as a sink plays; the defaults change nothing.

    `equalizer` maps band centres in Hz to gains in dB, each an octave wide; `bass`
    is a gain in dB below 100 Hz; `speed` keeps the pitch and `pitch` the tempo.
    """

    equalizer: Mapping[float, float] = MappingProxyType({})
    bass: float = 0.0
    speed: float = 1.0
    pitch: float = 1.0

    def __post_init__(self) -> None:
        if len(self.equalizer) > _MAX_BANDS:
            raise ValueError(
                f"equalizer takes at most {_MAX_BANDS} bands, got {len(self.equalizer)}"
            )
        for frequency, gain in self.equalizer.items():
            if not _MIN_BAND_FREQUENCY <= frequency <= _MAX_BAND_FREQUENCY:
                raise ValueError(
                    f"equalizer frequencies must be from {_MIN_BAND_FREQUENCY:g} to "
                    f"{_MAX_BAND_FREQUENCY:g} Hz, got {frequency}"
                )
            _check_gain("equalizer gains", gain)
        _check_gain("bass", self.bass)
        for name, value in (("speed", self.speed), ("pitch", self.pitch)):
            if not _MIN_SPEED <= value <= _MAX_SPEED:
                raise ValueError(
                    f"{name} must be from {_MIN_SPEED:g} to {_MAX_SPEED:g}, got {value}"
                )


def _check_gain(name: str, gain: float) -> None:
    if not -_MAX_GAIN <= gain <= _MAX_GAIN:
        raise ValueError(
            f"{name} must be from -{_MAX_GAIN:g} to {_MAX_GAIN:g} dB, got {gain}"
        )


_NO_FILTERS = Filters()


@dataclass(frozen=True, slots=True, kw_only=True)
class Defaults:
    """Voice settings every connection starts from."""

    self_deaf: bool = False
    self_mute: bool = False
    timeout: float = 10.0
    server_change_delay: float = 0.0
    encoder: EncoderSettings = EncoderSettings()
    speaking_flags: SpeakingFlags = SpeakingFlags.MICROPHONE
    stage: StageRole = "listen"
    sink_buffer: float = 0.2
    stall: SinkStall = "hold"
    speech_hangover: float = 0.2
    speech_activity: bool = True
    stall_after: float = 2.0
    receive_buffer: float = 2.0
    jitter: float = 0.1
    overflow: Overflow = "drop_oldest"
    receive_stall: ReceiveStall = "wait"
    crossfade: float = 0.0
    history: int = 0

    def __post_init__(self) -> None:
        if not (math.isfinite(self.timeout) and self.timeout > 0):
            raise ValueError(
                f"timeout must be a positive number of seconds, got {self.timeout}"
            )
        if not (
            math.isfinite(self.server_change_delay) and self.server_change_delay >= 0
        ):
            raise ValueError(
                "server_change_delay must be a number of seconds of at least 0, "
                f"got {self.server_change_delay}"
            )
        if not self.speaking_flags:
            raise ValueError("speaking_flags must set at least one flag")
        _check_choice("stage", self.stage, _STAGE_ROLES)
        if not _MIN_BUFFER <= self.sink_buffer <= _MAX_BUFFER:
            raise ValueError(
                f"sink_buffer must be from {_MIN_BUFFER} to {_MAX_BUFFER} seconds, "
                f"got {self.sink_buffer}"
            )
        _check_choice("stall", self.stall, _native.SINK_STALLS)
        if not _MIN_SPEECH_HANGOVER <= self.speech_hangover <= _MAX_SPEECH_HANGOVER:
            raise ValueError(
                f"speech_hangover must be from {_MIN_SPEECH_HANGOVER} to "
                f"{_MAX_SPEECH_HANGOVER} seconds, got {self.speech_hangover}"
            )
        _check_stall_after(self.stall_after)
        if not _MIN_BUFFER <= self.receive_buffer <= _MAX_BUFFER:
            raise ValueError(
                f"receive_buffer must be from {_MIN_BUFFER} to {_MAX_BUFFER} seconds, "
                f"got {self.receive_buffer}"
            )
        if not 0.0 <= self.jitter <= _MAX_JITTER:
            raise ValueError(
                f"jitter must be from 0 to {_MAX_JITTER} seconds, got {self.jitter}"
            )
        _check_choice("overflow", self.overflow, _native.OVERFLOWS)
        _check_crossfade(self.crossfade)
        if self.history < 0:
            raise ValueError(f"history must be at least 0, got {self.history}")
        _check_choice("receive_stall", self.receive_stall, _native.RECEIVE_STALLS)


@dataclass(frozen=True, slots=True)
class Latency:
    """Voice WebSocket and UDP round trips in seconds, None until measured."""

    ws: float | None
    udp: float | None
