import math
from dataclasses import dataclass
from typing import Literal

from bostie.enums import SpeakingFlags

SAMPLE_RATE = 48_000
MIN_RATE = 8_000
MAX_RATE = 192_000
MAX_RESAMPLER_BLOCK = 960
MIN_BITRATE = 500
MAX_BITRATE = 512_000
MAX_COMPLEXITY = 10
MIN_BUFFER = 0.02
MAX_BUFFER = 60.0
MIN_SPEECH_HANGOVER = 0.02
MAX_SPEECH_HANGOVER = 10.0
MAX_JITTER = 1.0
DECODE_RATES = frozenset({8_000, 12_000, 16_000, 24_000, 48_000})


@dataclass(frozen=True, slots=True)
class PCM:
    """Raw audio samples, interleaved, little endian."""

    rate: int = SAMPLE_RATE
    channels: Literal[1, 2] = 2
    sample: Literal["s16le", "f32le"] = "s16le"

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
    """Opus encoder settings; `bitrate=None` follows the channel."""

    bitrate: int | None = None
    application: Literal["audio", "voip", "lowdelay"] = "audio"
    fec: bool = True
    packet_loss: float = 0.1
    complexity: int = 10

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


@dataclass(frozen=True, slots=True, kw_only=True)
class Defaults:
    """Voice settings every connection starts from."""

    self_deaf: bool = False
    self_mute: bool = False
    timeout: float = 10.0
    server_change_delay: float = 0.0
    encoder: EncoderSettings = EncoderSettings()
    speaking_flags: SpeakingFlags = SpeakingFlags.MICROPHONE
    stage: Literal["listen", "request", "speak"] = "listen"
    sink_buffer: float = 0.2
    stall: Literal["hold", "drop"] = "hold"
    speech_hangover: float = 0.2
    speech_activity: bool = True
    receive_buffer: float = 2.0
    jitter: float = 0.1
    overflow: Literal["drop_oldest"] = "drop_oldest"
    receive_stall: Literal["wait"] = "wait"

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
        if self.overflow != "drop_oldest":
            raise ValueError(f"overflow must be 'drop_oldest', got {self.overflow!r}")
        if self.receive_stall != "wait":
            raise ValueError(
                f"receive_stall must be 'wait', got {self.receive_stall!r}"
            )


@dataclass(frozen=True, slots=True)
class Latency:
    """Voice WebSocket and UDP round trips in seconds, None until measured."""

    ws: float | None
    udp: float | None
