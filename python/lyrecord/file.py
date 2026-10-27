from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class File:
    """A file to upload with a message."""

    name: str
    data: bytes
    description: str | None = None
