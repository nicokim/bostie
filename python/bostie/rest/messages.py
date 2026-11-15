import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Literal, Required, TypedDict, Unpack

import msgspec

from bostie._aio import spawn
from bostie.cache import Cache
from bostie.enums import JSONErrorCode
from bostie.errors import BostieError, NotFound
from bostie.http import HTTPClient
from bostie.models import (
    AllowedMentions,
    Channel,
    EditParts,
    Message,
    MessageParts,
    MessagePin,
    MessageReference,
    MessageSearchResults,
    SharedClientTheme,
    ThreadMember,
    WebhookMessage,
    decode_message,
)
from bostie.models.base import check_keys, decoder
from bostie.models.message import EDIT_PARTS, in_guild, message_body
from bostie.rest.base import ResourceGroup, each, wire


class MessageCreate(MessageParts, total=False):
    """The parts of a message to create; `message_reference` can forward one."""

    nonce: int | str
    enforce_nonce: bool
    sticker_ids: Sequence[int]
    message_reference: MessageReference
    shared_client_theme: SharedClientTheme


class MessageList(TypedDict, total=False):
    """Where `messages.list` starts, at most one of them, and how many it yields."""

    before: int | datetime
    after: int | datetime
    around: int | datetime
    limit: int


class MessageBulkDelete(TypedDict, total=False):
    messages: Required[Sequence[int]]


class MessageListPins(TypedDict, total=False):
    before: datetime
    limit: int


class MessageSearch(TypedDict, total=False):
    """Filters of a guild message search; `has` and the types take a `-` to negate."""

    limit: int
    offset: int
    max_id: int
    min_id: int
    slop: int
    content: str
    channel_id: Sequence[int]
    author_type: Sequence[str]
    author_id: Sequence[int]
    mentions: Sequence[int]
    mentions_role_id: Sequence[int]
    mention_everyone: bool
    replied_to_user_id: Sequence[int]
    replied_to_message_id: Sequence[int]
    pinned: bool
    has: Sequence[str]
    embed_type: Sequence[str]
    embed_provider: Sequence[str]
    link_hostname: Sequence[str]
    attachment_filename: Sequence[str]
    attachment_extension: Sequence[str]
    sort_by: Literal["timestamp", "relevance"]
    sort_order: Literal["asc", "desc"]
    include_nsfw: bool


MESSAGE_CREATE = MessageCreate.__optional_keys__
BULK_DELETE_MAX = 100
BULK_DELETABLE_FOR = timedelta(days=14) - timedelta(minutes=1)

log = logging.getLogger("bostie.rest")


def delete_later(
    tasks: set[asyncio.Task[None]],
    delay: float,
    delete: Callable[[], Awaitable[None]],
) -> None:
    """Run `delete` after `delay` seconds as a task in `tasks`, its owner's."""
    spawn(tasks, _delete_after(delay, delete), eager=True)


async def _delete_after(delay: float, delete: Callable[[], Awaitable[None]]) -> None:
    await asyncio.sleep(delay)
    try:
        await delete()
    except NotFound:
        pass
    except BostieError as error:
        log.warning("deleting a message after %s s failed: %s", delay, error)


class _Pin(msgspec.Struct):
    pinned_at: datetime
    message: msgspec.Raw


class _Indexing(msgspec.Struct):
    code: int = 0
    retry_after: float = 0


class _Found(msgspec.Struct, kw_only=True):
    doing_deep_historical_index: bool
    documents_indexed: int | None = None
    total_results: int
    messages: tuple[tuple[msgspec.Raw, ...], ...]
    threads: tuple[Channel, ...] = ()
    members: tuple[ThreadMember, ...] = ()


class Messages(ResourceGroup):
    def __init__(
        self,
        http: HTTPClient,
        allowed_mentions: AllowedMentions,
        cache: Cache | None,
        deletions: set[asyncio.Task[None]],
    ) -> None:
        super().__init__(http, allowed_mentions, cache)
        self._deletions = deletions

    async def create(
        self,
        channel_id: int,
        /,
        content: str | None = None,
        *,
        reply_to: int | None = None,
        delete_after: float | None = None,
        **parts: Unpack[MessageCreate],
    ) -> Message:
        """Send a message; `reply_to` takes the id of a message in the same channel.

        `delete_after` deletes it that many seconds later, unless the bot stops first.
        """
        check_keys(parts, MESSAGE_CREATE, "message parts")
        body, files = message_body(
            content, parts, reply_to=reply_to, mentions=self._allowed_mentions
        )
        message = await self._call(
            Message,
            "POST",
            "/channels/{channel_id}/messages",
            json=body,
            files=files,
            channel_id=channel_id,
        )
        if delete_after is not None:
            delete_later(
                self._deletions,
                delete_after,
                partial(self.delete, channel_id, message.id),
            )
        return self._in_guild(message)

    async def edit(
        self, channel_id: int, message_id: int, /, **parts: Unpack[EditParts]
    ) -> Message | WebhookMessage:
        """Edit a message: a part not given stays and None clears it.

        With `files` or `attachments`, only the listed `attachments` stay.
        """
        check_keys(parts, EDIT_PARTS, "message parts")
        body, files = message_body(None, parts, mentions=self._allowed_mentions)
        message = await self._call(
            decode_message,
            "PATCH",
            "/channels/{channel_id}/messages/{message_id}",
            json=body,
            files=files,
            channel_id=channel_id,
            message_id=message_id,
        )
        return self._in_guild(message)

    async def delete(
        self, channel_id: int, message_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http.send(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}",
            reason=reason,
            channel_id=channel_id,
            message_id=message_id,
        )

    async def bulk_delete(
        self,
        channel_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[MessageBulkDelete],
    ) -> None:
        await self._http.send(
            "POST",
            "/channels/{channel_id}/messages/bulk-delete",
            json=wire(options, MessageBulkDelete),
            reason=reason,
            channel_id=channel_id,
        )

    async def purge(
        self,
        channel_id: int,
        /,
        *,
        limit: int | None = 100,
        check: Callable[[Message | WebhookMessage], bool] | None = None,
        before: int | datetime | None = None,
        after: int | datetime | None = None,
        bulk: bool = True,
        reason: str | None = None,
    ) -> list[Message | WebhookMessage]:
        """Delete the deletable messages among the last `limit` that pass `check`."""
        window: MessageList = {}
        if limit is not None:
            window["limit"] = limit
        if before is not None:
            window["before"] = before
        if after is not None:
            window["after"] = after
        deleted: list[Message | WebhookMessage] = []
        batch: list[int] = []
        async for message in self.list(channel_id, **window):
            if not message.type.deletable or (check is not None and not check(message)):
                continue
            deleted.append(message)
            if bulk and datetime.now(UTC) - message.created_at < BULK_DELETABLE_FOR:
                batch.append(message.id)
                if len(batch) == BULK_DELETE_MAX:
                    await self._delete_batch(channel_id, batch, reason)
                    batch = []
            else:
                await self.delete(channel_id, message.id, reason=reason)
        await self._delete_batch(channel_id, batch, reason)
        return deleted

    async def _delete_batch(
        self, channel_id: int, batch: list[int], reason: str | None
    ) -> None:
        if len(batch) == 1:
            await self.delete(channel_id, batch[0], reason=reason)
        elif batch:
            await self.bulk_delete(channel_id, messages=batch, reason=reason)

    async def crosspost(self, channel_id: int, message_id: int, /) -> Message:
        message = await self._call(
            Message,
            "POST",
            "/channels/{channel_id}/messages/{message_id}/crosspost",
            channel_id=channel_id,
            message_id=message_id,
        )
        return self._in_guild(message)

    async def fetch(
        self, channel_id: int, message_id: int, /
    ) -> Message | WebhookMessage:
        message = await self._call(
            decode_message,
            "GET",
            "/channels/{channel_id}/messages/{message_id}",
            channel_id=channel_id,
            message_id=message_id,
        )
        return self._in_guild(message)

    def list(
        self, channel_id: int, /, **options: Unpack[MessageList]
    ) -> AsyncIterator[Message | WebhookMessage]:
        """Walk a channel's messages newest first, or oldest first with `after`."""
        pages = self._paginate(
            "/channels/{channel_id}/messages",
            wire(options, MessageList),
            decodes=each(self._message),
            items=tuple,
            cursor=lambda message: message.id,
            page_size=100,
            channel_id=channel_id,
        )
        forward = "after" in options
        return (
            message
            async for page in pages
            for message in (reversed(page) if forward else page)
        )

    def list_pins(
        self, channel_id: int, /, **options: Unpack[MessageListPins]
    ) -> AsyncIterator[MessagePin]:
        """Walk a channel's pins, the latest pinned first."""
        pages = self._paginate(
            "/channels/{channel_id}/messages/pins",
            wire(options, MessageListPins),
            decodes=each(self._pin, "items"),
            items=tuple,
            cursor=lambda pin: pin.pinned_at,
            page_size=50,
            time_cursor=True,
            channel_id=channel_id,
        )
        return (pin async for page in pages for pin in page)

    async def pin(
        self, channel_id: int, message_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http.send(
            "PUT",
            "/channels/{channel_id}/messages/pins/{message_id}",
            reason=reason,
            channel_id=channel_id,
            message_id=message_id,
        )

    async def unpin(
        self, channel_id: int, message_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http.send(
            "DELETE",
            "/channels/{channel_id}/messages/pins/{message_id}",
            reason=reason,
            channel_id=channel_id,
            message_id=message_id,
        )

    async def search(
        self, guild_id: int, /, **options: Unpack[MessageSearch]
    ) -> MessageSearchResults:
        """Search messages, waiting out indexing; bound it with asyncio.timeout()."""
        query = wire(options, MessageSearch)
        while True:
            found = await self._call(
                partial(_search_results, guild_id),
                "GET",
                "/guilds/{guild_id}/messages/search",
                query=query,
                guild_id=guild_id,
            )
            if isinstance(found, MessageSearchResults):
                return found
            await asyncio.sleep(found.retry_after or 1)

    def _message(self, data: bytes) -> Message | WebhookMessage:
        return self._in_guild(decode_message(data))

    def _pin(self, data: bytes) -> MessagePin:
        pin = decoder(_Pin).decode(data)
        message = self._message(bytes(pin.message))
        return MessagePin(pinned_at=pin.pinned_at, message=message, raw_json=data)


def _search_results(guild_id: int, data: bytes) -> MessageSearchResults | _Indexing:
    indexing = decoder(_Indexing).decode(data)
    if indexing.code == JSONErrorCode.INDEX_NOT_YET_AVAILABLE:
        return indexing
    found = decoder(_Found).decode(data)
    return MessageSearchResults(
        doing_deep_historical_index=found.doing_deep_historical_index,
        documents_indexed=found.documents_indexed,
        total_results=found.total_results,
        messages=tuple(
            in_guild(decode_message(bytes(raw)), guild_id)
            for group in found.messages
            for raw in group
        ),
        threads=found.threads,
        members=found.members,
        raw_json=data,
    )
