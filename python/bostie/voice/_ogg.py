import math
import struct
import zlib

from bostie.voice.formats import SAMPLE_RATE

PAGE_SAMPLES = SAMPLE_RATE
MAX_SEGMENTS = 255
MAX_PACKET = MAX_SEGMENTS * 255 - 1
BEGINNING_OF_STREAM = 0x02
END_OF_STREAM = 0x04
OPUS_HEAD = struct.pack("<8sBBHIhB", b"OpusHead", 1, 2, 0, SAMPLE_RATE, 0, 0)
OPUS_TAGS = struct.pack("<8sI6sI", b"OpusTags", 6, b"bostie", 0)
REVERSED_BITS = bytes(int(f"{value:08b}"[::-1], 2) for value in range(256))


class OggStream:
    """The Ogg pages of one Opus stream, OpusHead and OpusTags first."""

    def __init__(self, serial: int) -> None:
        self.serial = serial
        self.sequence = 0
        self.granule = 0
        self.samples = 0
        self.lacing = bytearray()
        self.body = bytearray()
        self.pages = bytearray()
        for flags, packet in ((BEGINNING_OF_STREAM, OPUS_HEAD), (0, OPUS_TAGS)):
            self.lacing += _lacing(packet)
            self.body += packet
            self.flush(flags)

    def add(self, packet: bytes, samples: int, count: int = 1) -> None:
        lacing = _lacing(packet)
        while count > 0:
            full = len(self.lacing) + len(lacing) > MAX_SEGMENTS
            if full or self.samples >= PAGE_SAMPLES:
                self.flush()
            fit = min(
                count,
                (MAX_SEGMENTS - len(self.lacing)) // len(lacing),
                math.ceil((PAGE_SAMPLES - self.samples) / samples),
            )
            self.lacing += lacing * fit
            self.body += packet * fit
            self.samples += samples * fit
            self.granule += samples * fit
            count -= fit

    def flush(self, flags: int = 0) -> None:
        header = struct.pack(
            "<4sBBqIIIB",
            b"OggS",
            0,
            flags,
            self.granule,
            self.serial,
            self.sequence,
            0,
            len(self.lacing),
        )
        page = bytearray(header) + self.lacing + self.body
        page[22:26] = _ogg_crc32(page)
        self.pages += page
        self.sequence += 1
        self.samples = 0
        self.lacing.clear()
        self.body.clear()

    def take(self) -> bytes:
        pages = bytes(self.pages)
        self.pages.clear()
        return pages


def _lacing(packet: bytes) -> bytes:
    return bytes([255] * (len(packet) // 255) + [len(packet) % 255])


def _ogg_crc32(page: bytearray) -> bytes:
    crc = zlib.crc32(page.translate(REVERSED_BITS), 0xFFFFFFFF) ^ 0xFFFFFFFF
    return crc.to_bytes(4, "big").translate(REVERSED_BITS)
