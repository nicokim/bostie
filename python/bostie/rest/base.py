import base64
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import datetime
from pathlib import PurePath
from typing import Any

import msgspec

from bostie.cache import Cache
from bostie.enums import Permissions
from bostie.file import File
from bostie.http import HTTPClient
from bostie.models import AllowedMentions
from bostie.models.base import check_keys, decoder
from bostie.models.message import PartialMessage, in_guild

CURSORS = ("before", "after", "around")
DATA_URI_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
}


class Group:
    """Base of the resource groups on `bot.rest`."""

    def __init__(
        self, http: HTTPClient, allowed_mentions: AllowedMentions, cache: Cache
    ) -> None:
        self._http = http
        self._allowed_mentions = allowed_mentions
        self._cache = cache

    def _in_guild[M: PartialMessage](self, message: M) -> M:
        channel = self._cache.channel(message.channel_id)
        return in_guild(message, None if channel is None else channel.guild_id)


def each[T](
    decode: Callable[[bytes], T], key: str | None = None
) -> Callable[[bytes], tuple[T, ...]]:
    """A decoder of a JSON array, or of the one under `key`, element by element."""

    def decode_each(data: bytes) -> tuple[T, ...]:
        array: bytes | msgspec.Raw = data
        if key is not None:
            wrapper = decoder(dict[str, msgspec.Raw]).decode(data)
            if key not in wrapper:
                raise msgspec.ValidationError(f"Object missing required field `{key}`")
            array = wrapper[key]
        raws = decoder(tuple[msgspec.Raw, ...]).decode(array)
        return tuple(decode(bytes(raw)) for raw in raws)

    return decode_each


def wire(options: Mapping[str, object], shape: type[Any]) -> dict[str, object]:
    """`options` checked against the TypedDict `shape`, ready for the wire.

    A File becomes a data URI and Permissions their string of bits.
    """
    check_keys(options, shape.__required_keys__ | shape.__optional_keys__, "options")
    return {name: _wire_value(value) for name, value in options.items()}


def _wire_value(value: object) -> object:
    match value:
        case File():
            return _data_uri(value)
        case Permissions():
            return str(value.value)
        case _:
            return value


def _data_uri(file: File) -> str:
    kind = DATA_URI_TYPES.get(PurePath(file.name).suffix.lower())
    if kind is None:
        raise ValueError(
            f"{file.name!r} must end in one of {', '.join(DATA_URI_TYPES)}"
        )
    return f"data:{kind};base64,{base64.b64encode(file.data).decode()}"


def paginate[P, I](
    http: HTTPClient,
    route: str,
    options: Mapping[str, Any],
    *,
    decode: Callable[[bytes], P],
    items: Callable[[P], Sequence[I]],
    cursor: Callable[[I], int | datetime],
    page_size: int,
    forward: bool = False,
    **params: object,
) -> AsyncIterator[P]:
    """Walk a GET route by its cursor up to `limit` items, or all of them without it."""
    given = [name for name in CURSORS if name in options]
    if len(given) > 1:
        raise ValueError(f"{' and '.join(given)} are mutually exclusive")
    direction = given[0] if given else "after" if forward else "before"
    limit: int | None = options.get("limit")

    async def pages() -> AsyncIterator[P]:
        query = {name: value for name, value in options.items() if name != "limit"}
        remaining = limit
        while remaining is None or remaining > 0:
            size = page_size if remaining is None else min(page_size, remaining)
            page = await http._decoded(
                decode, "GET", route, query=query | {"limit": size}, **params
            )
            found = items(page)
            if found:
                yield page
            if direction == "around" or len(found) < size:
                return
            if remaining is not None:
                remaining -= len(found)
            positions = [cursor(item) for item in found]
            query[direction] = (
                max(positions) if direction == "after" else min(positions)
            )

    return pages()
