from collections.abc import AsyncIterator
from functools import partial
from typing import TypedDict, Unpack

from bostie.enums import ReactionType
from bostie.models import User, decode
from bostie.models.components import AnyEmoji, partial_emoji
from bostie.rest.base import Group, each, paginate, wire


class ReactionList(TypedDict, total=False):
    type: ReactionType
    after: int
    limit: int


class Reactions(Group):
    """Reactions on a message; `emoji_name` is an emoji str, Emoji or PartialEmoji."""

    async def create(
        self, channel_id: int, message_id: int, emoji_name: AnyEmoji, /
    ) -> None:
        await self._http._request(
            "PUT",
            "/channels/{channel_id}/messages/{message_id}/reactions/{emoji_name}/@me",
            channel_id=channel_id,
            message_id=message_id,
            emoji_name=_route_emoji(emoji_name),
        )

    async def delete(
        self, channel_id: int, message_id: int, emoji_name: AnyEmoji, user_id: int, /
    ) -> None:
        await self._http._request(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}/reactions/{emoji_name}/{user_id}",
            channel_id=channel_id,
            message_id=message_id,
            emoji_name=_route_emoji(emoji_name),
            user_id=user_id,
        )

    async def delete_all(self, channel_id: int, message_id: int, /) -> None:
        await self._http._request(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}/reactions",
            channel_id=channel_id,
            message_id=message_id,
        )

    async def delete_all_for_emoji(
        self, channel_id: int, message_id: int, emoji_name: AnyEmoji, /
    ) -> None:
        await self._http._request(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}/reactions/{emoji_name}",
            channel_id=channel_id,
            message_id=message_id,
            emoji_name=_route_emoji(emoji_name),
        )

    async def delete_own(
        self, channel_id: int, message_id: int, emoji_name: AnyEmoji, /
    ) -> None:
        await self._http._request(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}/reactions/{emoji_name}/@me",
            channel_id=channel_id,
            message_id=message_id,
            emoji_name=_route_emoji(emoji_name),
        )

    def list(
        self,
        channel_id: int,
        message_id: int,
        emoji_name: AnyEmoji,
        /,
        **options: Unpack[ReactionList],
    ) -> AsyncIterator[User]:
        """Walk the users who reacted with an emoji."""
        pages = paginate(
            self._http,
            "/channels/{channel_id}/messages/{message_id}/reactions/{emoji_name}",
            wire(options, ReactionList),
            decode=each(partial(decode, User)),
            items=tuple,
            cursor=lambda user: user.id,
            page_size=100,
            forward=True,
            channel_id=channel_id,
            message_id=message_id,
            emoji_name=_route_emoji(emoji_name),
        )
        return (user async for page in pages for user in page)


def _route_emoji(emoji: AnyEmoji) -> str:
    parsed = partial_emoji(emoji)
    return f"{parsed.name}:{parsed.id}" if parsed.id is not None else str(parsed.name)
