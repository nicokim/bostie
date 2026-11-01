from collections.abc import AsyncIterator
from functools import partial
from typing import TypedDict, Unpack

from bostie.models import (
    EditParts,
    Message,
    MessageParts,
    WebhookMessage,
    build_message,
    decode,
    decode_message,
)
from bostie.models.base import check_keys
from bostie.models.message import MESSAGE_PARTS, message_body
from bostie.rest.base import Group, each, paginate, wire


class MessageList(TypedDict, total=False):
    """Where `messages.list` starts, at most one of them, and how many it yields."""

    before: int
    after: int
    around: int
    limit: int


class Messages(Group):
    async def create(
        self,
        channel_id: int,
        /,
        content: str | None = None,
        *,
        reply_to: int | None = None,
        **parts: Unpack[MessageParts],
    ) -> Message:
        """Send a message; `reply_to` takes the id of a message in the same channel."""
        check_keys(parts, MESSAGE_PARTS, "message parts")
        parts.setdefault("allowed_mentions", self._allowed_mentions)
        body, files = message_body(content, parts, reply_to=reply_to)
        message = await self._http._decoded(
            partial(decode, Message),
            "POST",
            "/channels/{channel_id}/messages",
            json=body,
            files=files,
            channel_id=channel_id,
        )
        return self._in_guild(message)

    async def edit(
        self,
        channel_id: int,
        message_id: int,
        /,
        content: str | None = None,
        **parts: Unpack[EditParts],
    ) -> Message | WebhookMessage:
        """Edit a message; only the parts passed change.

        With `files` or `attachments`, only the listed `attachments` stay.
        """
        parts.setdefault("allowed_mentions", self._allowed_mentions)
        body, files = build_message(content, **parts)
        message = await self._http._decoded(
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
        await self._http._request(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}",
            reason=reason,
            channel_id=channel_id,
            message_id=message_id,
        )

    async def fetch(
        self, channel_id: int, message_id: int, /
    ) -> Message | WebhookMessage:
        message = await self._http._decoded(
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
        pages = paginate(
            self._http,
            "/channels/{channel_id}/messages",
            wire(options, MessageList),
            decode=each(self._message),
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

    def _message(self, data: bytes) -> Message | WebhookMessage:
        return self._in_guild(decode_message(data))
