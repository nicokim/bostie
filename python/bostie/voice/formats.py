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


@dataclass(frozen=True, slots=True, kw_only=True)
class Encoder:
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
    encoder: Encoder = Encoder()
    speaking_flags: SpeakingFlags = SpeakingFlags.MICROPHONE
    stage: Literal["listen", "request", "speak"] = "listen"
    sink_buffer: float = 0.2
    stall: Literal["hold", "drop"] = "hold"

    def __post_init__(self) -> None:
        if not (math.isfinite(self.timeout) and self.timeout > 0):
            raise ValueError(
                f"timeout must be a positive number of seconds, got {self.timeout}"
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


@dataclass(frozen=True, slots=True)
class Latency:
    """Voice WebSocket and UDP round trips in seconds, None until measured."""

    ws: float | None
    udp: float | None
