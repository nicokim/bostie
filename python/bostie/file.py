from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class File:
    """A file to upload with a message; `spoiler` blurs it until clicked.

    With `duration_secs` and `waveform`, an audio file makes a voice message.
    """

    name: str
    data: bytes
    description: str | None = None
    spoiler: bool = False
    title: str | None = None
    duration_secs: float | None = None
    waveform: bytes | None = None
