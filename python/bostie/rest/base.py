import base64
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import datetime
from functools import partial
from pathlib import PurePath
from typing import Any, cast

import msgspec

from bostie.cache import Cache
from bostie.enums import Permissions
from bostie.file import File
from bostie.http import HTTPClient, decode_answer
from bostie.models import AllowedMentions
from bostie.models.base import Model, check_keys, decode, decoder
from bostie.models.message import PartialMessage, in_guild
from bostie.snowflake import time_snowflake

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


type Decodes[T] = type[T] | Callable[[bytes], T]


class ResourceGroup:
    """Base of the resource groups on `bot.rest`."""

    def __init__(
        self, http: HTTPClient, allowed_mentions: AllowedMentions, cache: Cache | None
    ) -> None:
        self._http = http
        self._allowed_mentions = allowed_mentions
        self._cache = cache

    def _in_guild[M: PartialMessage](self, message: M) -> M:
        channel = (
            None if self._cache is None else self._cache.channel(message.channel_id)
        )
        return in_guild(message, None if channel is None else channel.guild_id)

    async def _call[T](
        self, decodes: Decodes[T], method: str, route: str, /, **request: Any
    ) -> T:
        data = await self._http.send(method, route, **request)
        return decode_answer(decoding(decodes), data, took_effect=method != "GET")

    def _paginate[P, I](
        self,
        route: str,
        options: Mapping[str, Any],
        *,
        decodes: Decodes[P],
        items: Callable[[P], Sequence[I]],
        cursor: Callable[[I], int | datetime],
        page_size: int,
        forward: bool = False,
        time_cursor: bool = False,
        **params: object,
    ) -> AsyncIterator[P]:
        given = [name for name in CURSORS if name in options]
        if len(given) > 1:
            raise ValueError(f"{' and '.join(given)} are mutually exclusive")
        direction = given[0] if given else "after" if forward else "before"
        limit: int | None = options.get("limit")

        async def pages() -> AsyncIterator[P]:
            query = {
                name: _position(name, value, time_cursor=time_cursor)
                for name, value in options.items()
                if name != "limit"
            }
            remaining = limit
            while remaining is None or remaining > 0:
                size = page_size if remaining is None else min(page_size, remaining)
                page = await self._call(
                    decodes, "GET", route, query=query | {"limit": size}, **params
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


def decoding[T](decodes: Decodes[T]) -> Callable[[bytes], T]:
    """A model class's decoder, which keeps the payload as raw JSON, or `decodes`."""
    if not isinstance(decodes, type):
        return decodes
    if issubclass(decodes, Model):
        return cast("Callable[[bytes], T]", partial(decode, decodes))
    typed: msgspec.json.Decoder[T] = decoder(cast("Any", decodes))
    return typed.decode


def each[T](
    decodes: Decodes[T], key: str | None = None
) -> Callable[[bytes], tuple[T, ...]]:
    """A decoder of a JSON array, or of the one under `key`, element by element."""
    decode_item = decoding(decodes)

    def decode_each(data: bytes) -> tuple[T, ...]:
        array: bytes | msgspec.Raw = data
        if key is not None:
            wrapper = decoder(dict[str, msgspec.Raw]).decode(data)
            if key not in wrapper:
                raise msgspec.ValidationError(f"Object missing required field `{key}`")
            array = wrapper[key]
        raws = decoder(tuple[msgspec.Raw, ...]).decode(array)
        return tuple(decode_item(bytes(raw)) for raw in raws)

    return decode_each


def wire(options: Mapping[str, object], shape: type[Any]) -> dict[str, object]:
    """`options` checked against the TypedDict `shape`, ready for the wire.

    A File becomes a data URI, and Permissions at any depth their string of bits.
    """
    check_keys(options, shape.__required_keys__ | shape.__optional_keys__, "options")
    return {
        name: _data_uri(value) if isinstance(value, File) else _permission_bits(value)
        for name, value in options.items()
    }


def _permission_bits(value: object) -> object:
    match value:
        case Permissions():
            return str(value.value)
        case msgspec.Struct():
            fields = value.__struct_fields__
            bits = {name: _permission_bits(getattr(value, name)) for name in fields}
            return msgspec.structs.replace(value, **bits)
        case Mapping():
            mapping = cast("Mapping[object, object]", value)
            return {key: _permission_bits(item) for key, item in mapping.items()}
        case tuple() | list():
            items = [_permission_bits(item) for item in cast("Sequence[object]", value)]
            return items if isinstance(value, list) else tuple(items)
        case _:
            return value


def _data_uri(file: File) -> str:
    kind = DATA_URI_TYPES.get(PurePath(file.name).suffix.lower())
    if kind is None:
        raise ValueError(
            f"{file.name!r} must end in one of {', '.join(DATA_URI_TYPES)}"
        )
    return f"data:{kind};base64,{base64.b64encode(file.data).decode()}"


def _position(name: str, value: object, *, time_cursor: bool) -> object:
    if time_cursor or name not in CURSORS or not isinstance(value, datetime):
        return value
    return time_snowflake(value, high=name == "after")
