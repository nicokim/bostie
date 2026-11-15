from collections.abc import Buffer
from dataclasses import replace

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
            encoder=replace(encoder, bitrate=encoder.bitrate or DEFAULT_BITRATE),
        )

    def encode(self, data: Buffer) -> list[bytes]:
        """Every complete packet so far; the remainder waits for the next call."""
        return self._native.encode(memoryview(data).cast("B"))

    def flush(self) -> list[bytes]:
        """Pad the remainder with silence and return the last packets."""
        return self._native.flush()
