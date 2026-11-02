from collections.abc import Buffer

from bostie import _native
from bostie.voice.formats import DEFAULT_PCM, PCM, EncoderSettings

DEFAULT_BITRATE = 64_000
DEFAULT_ENCODER = EncoderSettings()


class OpusEncoder:
    """Encodes PCM into the 20 ms 48 kHz stereo packets an `Opus()` sink passes through.

    `bitrate=None` means 64 kbps here, since there is no channel to follow.
    """

    def __init__(
        self, format: PCM = DEFAULT_PCM, *, encoder: EncoderSettings = DEFAULT_ENCODER
    ) -> None:
        self._native = _native.OpusEncoder(
            rate=format.rate,
            channels=format.channels,
            sample=format.sample,
            bitrate=encoder.bitrate or DEFAULT_BITRATE,
            application=encoder.application,
            fec=encoder.fec,
            packet_loss=percent(encoder.packet_loss),
            complexity=encoder.complexity,
            bandwidth=encoder.bandwidth,
            signal=encoder.signal_type,
        )

    def encode(self, data: Buffer) -> list[bytes]:
        """Every complete packet so far; the remainder waits for the next call."""
        return self._native.encode(memoryview(data).cast("B"))

    def flush(self) -> list[bytes]:
        """Pad the remainder with silence and return the last packets."""
        return self._native.flush()


def percent(fraction: float) -> int:
    return round(fraction * 100)
