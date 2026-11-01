from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from bostie.cache import Cache
from bostie.http import HTTPClient
from bostie.models import AllowedMentions
from bostie.models.message import PartialMessage, in_guild

CURSORS = ("before", "after", "around")


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
