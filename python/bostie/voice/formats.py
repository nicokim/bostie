import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypedDict, get_args

from bostie import _native
from bostie.enums import SpeakingFlags

SAMPLE_RATE = _native.SAMPLE_RATE
MIN_RATE = _native.MIN_RATE
MAX_RATE = _native.MAX_RATE
MAX_RESAMPLER_BLOCK = _native.MAX_RESAMPLER_BLOCK
MIN_BITRATE = _native.MIN_BITRATE
MAX_BITRATE = _native.MAX_BITRATE
MAX_COMPLEXITY = _native.MAX_COMPLEXITY
MIN_BUFFER = _native.MIN_BUFFER
MAX_BUFFER = _native.MAX_BUFFER
MIN_SPEECH_HANGOVER = _native.MIN_SPEECH_HANGOVER
MAX_SPEECH_HANGOVER = _native.MAX_SPEECH_HANGOVER
MAX_JITTER = _native.MAX_JITTER
MAX_BANDS = _native.MAX_BANDS
MIN_BAND_FREQUENCY = _native.MIN_BAND_FREQUENCY
MAX_BAND_FREQUENCY = _native.MAX_BAND_FREQUENCY
MAX_GAIN = _native.MAX_GAIN
MIN_SPEED = _native.MIN_SPEED
MAX_SPEED = _native.MAX_SPEED
DECODE_RATES = frozenset(_native.DECODE_RATES)

type Overflow = Literal["drop_oldest", "drop_newest"]
type ReceiveStall = Literal["wait", "end"]
type SinkStall = Literal["hold", "drop"]
type OpusApplication = Literal["audio", "voip", "lowdelay"]
type OpusBandwidth = Literal["auto", "narrow", "medium", "wide", "superwide", "full"]
type OpusSignal = Literal["auto", "voice", "music"]
type Sample = Literal["s16le", "f32le"]
type StageRole = Literal["listen", "request", "speak"]

SAMPLE_BYTES: dict[Sample, int] = {"s16le": 2, "f32le": 4}


def check_crossfade(seconds: float) -> float:
    if not 0.0 <= seconds <= MAX_BUFFER:
        raise ValueError(
            f"crossfade must be from 0 to {MAX_BUFFER} seconds, got {seconds}"
        )
    return seconds


@dataclass(frozen=True, slots=True)
class PCM:
    """Raw audio samples, interleaved, little endian."""

    rate: int = SAMPLE_RATE
    channels: Literal[1, 2] = 2
    sample: Sample = "s16le"

    def __post_init__(self) -> None:
        if not MIN_RATE <= self.rate <= MAX_RATE:
            raise ValueError(
                f"sample rate must be from {MIN_RATE} to {MAX_RATE} Hz, got {self.rate}"
            )
        if SAMPLE_RATE // math.gcd(self.rate, SAMPLE_RATE) > MAX_RESAMPLER_BLOCK:
            raise ValueError(
                f"sample rate {self.rate} Hz does not resample to {SAMPLE_RATE} Hz "
                f"in 20 ms blocks: {SAMPLE_RATE} / gcd(rate, {SAMPLE_RATE}) must be "
                f"at most {MAX_RESAMPLER_BLOCK}"
            )
        if self.channels not in (1, 2):
            raise ValueError(f"channels must be 1 or 2, got {self.channels}")
        if self.sample not in ("s16le", "f32le"):
            raise ValueError(f"sample must be 's16le' or 'f32le', got {self.sample!r}")


DEFAULT_PCM = PCM()


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
        if self.bitrate is not None and not MIN_BITRATE <= self.bitrate <= MAX_BITRATE:
            raise ValueError(
                f"bitrate must be from {MIN_BITRATE} to {MAX_BITRATE} bits per second, "
                f"got {self.bitrate}"
            )
        if self.application not in ("audio", "voip", "lowdelay"):
            raise ValueError(
                "application must be 'audio', 'voip' or 'lowdelay', "
                f"got {self.application!r}"
            )
        if not 0.0 <= self.packet_loss <= 1.0:
            raise ValueError(
                f"packet loss must be a fraction from 0 to 1, got {self.packet_loss}"
            )
        if not 0 <= self.complexity <= MAX_COMPLEXITY:
            raise ValueError(
                f"complexity must be from 0 to {MAX_COMPLEXITY}, got {self.complexity}"
            )
        if self.bandwidth not in get_args(OpusBandwidth.__value__):
            raise ValueError(
                "bandwidth must be 'auto', 'narrow', 'medium', 'wide', 'superwide' "
                f"or 'full', got {self.bandwidth!r}"
            )
        if self.signal_type not in get_args(OpusSignal.__value__):
            raise ValueError(
                "signal_type must be 'auto', 'voice' or 'music', "
                f"got {self.signal_type!r}"
            )


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
        if len(self.equalizer) > MAX_BANDS:
            raise ValueError(
                f"equalizer takes at most {MAX_BANDS} bands, got {len(self.equalizer)}"
            )
        for frequency, gain in self.equalizer.items():
            if not MIN_BAND_FREQUENCY <= frequency <= MAX_BAND_FREQUENCY:
                raise ValueError(
                    f"equalizer frequencies must be from {MIN_BAND_FREQUENCY:g} to "
                    f"{MAX_BAND_FREQUENCY:g} Hz, got {frequency}"
                )
            _check_gain("equalizer gains", gain)
        _check_gain("bass", self.bass)
        for name, value in (("speed", self.speed), ("pitch", self.pitch)):
            if not MIN_SPEED <= value <= MAX_SPEED:
                raise ValueError(
                    f"{name} must be from {MIN_SPEED:g} to {MAX_SPEED:g}, got {value}"
                )


def _check_gain(name: str, gain: float) -> None:
    if not -MAX_GAIN <= gain <= MAX_GAIN:
        raise ValueError(
            f"{name} must be from -{MAX_GAIN:g} to {MAX_GAIN:g} dB, got {gain}"
        )


NO_FILTERS = Filters()


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
        self._check_media()

    def _check_media(self) -> None:
        if not self.speaking_flags:
            raise ValueError("speaking_flags must set at least one flag")
        if self.stage not in ("listen", "request", "speak"):
            raise ValueError(
                f"stage must be 'listen', 'request' or 'speak', got {self.stage!r}"
            )
        if not MIN_BUFFER <= self.sink_buffer <= MAX_BUFFER:
            raise ValueError(
                f"sink_buffer must be from {MIN_BUFFER} to {MAX_BUFFER} seconds, "
                f"got {self.sink_buffer}"
            )
        if self.stall not in ("hold", "drop"):
            raise ValueError(f"stall must be 'hold' or 'drop', got {self.stall!r}")
        if not MIN_SPEECH_HANGOVER <= self.speech_hangover <= MAX_SPEECH_HANGOVER:
            raise ValueError(
                f"speech_hangover must be from {MIN_SPEECH_HANGOVER} to "
                f"{MAX_SPEECH_HANGOVER} seconds, got {self.speech_hangover}"
            )
        if not MIN_BUFFER <= self.receive_buffer <= MAX_BUFFER:
            raise ValueError(
                f"receive_buffer must be from {MIN_BUFFER} to {MAX_BUFFER} seconds, "
                f"got {self.receive_buffer}"
            )
        if not 0.0 <= self.jitter <= MAX_JITTER:
            raise ValueError(
                f"jitter must be from 0 to {MAX_JITTER} seconds, got {self.jitter}"
            )
        if self.overflow not in ("drop_oldest", "drop_newest"):
            raise ValueError(
                "overflow must be 'drop_oldest' or 'drop_newest', "
                f"got {self.overflow!r}"
            )
        check_crossfade(self.crossfade)
        if self.history < 0:
            raise ValueError(f"history must be at least 0, got {self.history}")
        if self.receive_stall not in ("wait", "end"):
            raise ValueError(
                f"receive_stall must be 'wait' or 'end', got {self.receive_stall!r}"
            )


class SinkOptions(TypedDict, total=False):
    """Settings for one sink; each overrides voice.Defaults."""

    buffer: float
    stall: SinkStall


class PacketReceiveOptions(TypedDict, total=False):
    """Settings for one receiver of voice.Packets(); each overrides voice.Defaults."""

    buffer: float
    overflow: Overflow
    stall: ReceiveStall


class ReceiveOptions(PacketReceiveOptions, total=False):
    """Settings for one receiver; each overrides voice.Defaults."""

    jitter: float


SINK_OPTIONS = SinkOptions.__optional_keys__
PACKET_RECEIVE_OPTIONS = PacketReceiveOptions.__optional_keys__
RECEIVE_OPTIONS = ReceiveOptions.__optional_keys__


@dataclass(frozen=True, slots=True)
class Latency:
    """Voice WebSocket and UDP round trips in seconds, None until measured."""

    ws: float | None
    udp: float | None
