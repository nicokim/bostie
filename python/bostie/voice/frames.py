import msgspec


class Frame(msgspec.Struct, frozen=True, gc=False):
    """A piece of one user's audio, or of the mix, on the receiver's 48 kHz clock."""

    user_id: int | None
    ssrc: int | None
    timestamp: int | None
    offset: int
    samples: int
    received_at: float
    data: bytes
    lost: bool


class Packet(msgspec.Struct, frozen=True, gc=False):
    """One decrypted Opus packet with its RTP fields and header extensions."""

    user_id: int
    ssrc: int
    sequence: int
    timestamp: int
    received_at: float
    opus: bytes
    extensions: tuple[tuple[int, bytes], ...]
