from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class File:
    """A file to upload with a message; `spoiler` blurs it until clicked."""

    name: str
    data: bytes
    description: str | None = None
    spoiler: bool = False
