from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import datetime
from functools import partial
from typing import Any, Required, TypedDict, Unpack

from bostie.enums import ChannelType, MessageFlags
from bostie.errors import UnexpectedResponse
from bostie.file import File
from bostie.models import (
    ActiveThreads,
    AllowedMentions,
    Channel,
    Component,
    Embed,
    ThreadMember,
    decode,
)
from bostie.models.base import Model, check_keys
from bostie.models.message import message_body
from bostie.rest.base import Group, each, paginate, wire

ARCHIVED_PAGE = 100


class ForumThreadMessage(TypedDict, total=False):
    """The first message of a forum or media thread."""

    content: str
    embeds: Sequence[Embed | Mapping[str, Any]]
    allowed_mentions: AllowedMentions
    components: Sequence[Component | Mapping[str, Any]]
    sticker_ids: Sequence[int]
    files: Sequence[File]
    flags: MessageFlags


class ThreadStart(TypedDict, total=False):
    name: Required[str]
    auto_archive_duration: int
    type: ChannelType
    invitable: bool
    rate_limit_per_user: int | None


class ThreadStartFromMessage(TypedDict, total=False):
    name: Required[str]
    auto_archive_duration: int
    rate_limit_per_user: int | None


class ThreadStartInForum(TypedDict, total=False):
    name: Required[str]
    auto_archive_duration: int
    rate_limit_per_user: int | None
    message: Required[ForumThreadMessage]
    applied_tags: Sequence[int]


class ThreadFetchMember(TypedDict, total=False):
    with_member: bool


class ThreadListMembers(TypedDict, total=False):
    after: int
    limit: int


class ThreadListArchived(TypedDict, total=False):
    before: datetime
    limit: int


class ThreadListJoinedPrivateArchived(TypedDict, total=False):
    before: int | datetime
    limit: int


FORUM_THREAD_MESSAGE = ForumThreadMessage.__optional_keys__


class Threads(Group):
    async def add_member(self, channel_id: int, user_id: int, /) -> None:
        await self._http._request(
            "PUT",
            "/channels/{channel_id}/thread-members/{user_id}",
            channel_id=channel_id,
            user_id=user_id,
        )

    async def fetch_member(
        self, channel_id: int, user_id: int, /, **options: Unpack[ThreadFetchMember]
    ) -> ThreadMember:
        return await self._http._decoded(
            partial(decode, ThreadMember),
            "GET",
            "/channels/{channel_id}/thread-members/{user_id}",
            query=wire(options, ThreadFetchMember),
            channel_id=channel_id,
            user_id=user_id,
        )

    async def join(self, channel_id: int, /) -> None:
        await self._http._request(
            "PUT", "/channels/{channel_id}/thread-members/@me", channel_id=channel_id
        )

    async def leave(self, channel_id: int, /) -> None:
        await self._http._request(
            "DELETE",
            "/channels/{channel_id}/thread-members/@me",
            channel_id=channel_id,
        )

    async def list_active(self, guild_id: int, /) -> ActiveThreads:
        return await self._http._decoded(
            partial(decode, ActiveThreads),
            "GET",
            "/guilds/{guild_id}/threads/active",
            guild_id=guild_id,
        )

    def list_joined_private_archived(
        self, channel_id: int, /, **options: Unpack[ThreadListJoinedPrivateArchived]
    ) -> AsyncIterator[Channel]:
        """Walk the archived private threads the bot joined, the newest first."""
        return self._archived(
            "/channels/{channel_id}/users/@me/threads/archived/private",
            wire(options, ThreadListJoinedPrivateArchived),
            lambda thread: thread.id,
            channel_id,
            time_cursor=False,
        )

    def list_members(
        self, channel_id: int, /, **options: Unpack[ThreadListMembers]
    ) -> AsyncIterator[ThreadMember]:
        """Walk a thread's members, each with its guild member."""
        pages = paginate(
            self._http,
            "/channels/{channel_id}/thread-members",
            wire(options, ThreadListMembers) | {"with_member": True},
            decode=each(partial(decode, ThreadMember)),
            items=tuple,
            cursor=_user_id,
            page_size=100,
            forward=True,
            channel_id=channel_id,
        )
        return (member async for page in pages for member in page)

    def list_private_archived(
        self, channel_id: int, /, **options: Unpack[ThreadListArchived]
    ) -> AsyncIterator[Channel]:
        """Walk a channel's archived private threads, the last archived first."""
        return self._archived(
            "/channels/{channel_id}/threads/archived/private",
            wire(options, ThreadListArchived),
            _archived_at,
            channel_id,
            time_cursor=True,
        )

    def list_public_archived(
        self, channel_id: int, /, **options: Unpack[ThreadListArchived]
    ) -> AsyncIterator[Channel]:
        """Walk a channel's archived public threads, the last archived first."""
        return self._archived(
            "/channels/{channel_id}/threads/archived/public",
            wire(options, ThreadListArchived),
            _archived_at,
            channel_id,
            time_cursor=True,
        )

    async def remove_member(self, channel_id: int, user_id: int, /) -> None:
        await self._http._request(
            "DELETE",
            "/channels/{channel_id}/thread-members/{user_id}",
            channel_id=channel_id,
            user_id=user_id,
        )

    async def start(
        self,
        channel_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ThreadStart],
    ) -> Channel:
        """Start a thread without a message; it is private unless `type` says not."""
        return await self._http._decoded(
            partial(decode, Channel),
            "POST",
            "/channels/{channel_id}/threads",
            json=wire(options, ThreadStart),
            reason=reason,
            channel_id=channel_id,
        )

    async def start_from_message(
        self,
        channel_id: int,
        message_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ThreadStartFromMessage],
    ) -> Channel:
        return await self._http._decoded(
            partial(decode, Channel),
            "POST",
            "/channels/{channel_id}/messages/{message_id}/threads",
            json=wire(options, ThreadStartFromMessage),
            reason=reason,
            channel_id=channel_id,
            message_id=message_id,
        )

    async def start_in_forum(
        self,
        channel_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ThreadStartInForum],
    ) -> Channel:
        """Start a forum or media thread with its first message."""
        body = wire(options, ThreadStartInForum)
        parts = dict(options["message"])
        check_keys(parts, FORUM_THREAD_MESSAGE, "message parts")
        parts.setdefault("allowed_mentions", self._allowed_mentions)
        body["message"], files = message_body(None, parts)
        return await self._http._decoded(
            partial(decode, Channel),
            "POST",
            "/channels/{channel_id}/threads",
            json=body,
            files=files,
            reason=reason,
            channel_id=channel_id,
        )

    def _archived(
        self,
        route: str,
        options: dict[str, Any],
        cursor: Callable[[Channel], int | datetime],
        channel_id: int,
        *,
        time_cursor: bool,
    ) -> AsyncIterator[Channel]:
        limit: int | None = options.get("limit")
        if limit is not None and limit % ARCHIVED_PAGE == 1:
            options["limit"] = limit + 1
        pages = paginate(
            self._http,
            route,
            options,
            decode=each(partial(decode, Channel), "threads"),
            items=tuple,
            cursor=cursor,
            page_size=ARCHIVED_PAGE,
            time_cursor=time_cursor,
            channel_id=channel_id,
        )
        return _first(pages, limit)


async def _first(
    pages: AsyncIterator[tuple[Channel, ...]], limit: int | None
) -> AsyncIterator[Channel]:
    count = 0
    async for page in pages:
        for thread in page:
            if count == limit:
                return
            count += 1
            yield thread


def _archived_at(thread: Channel) -> datetime:
    if thread.thread_metadata is None:
        raise _unpaged(thread, "an archived thread has no thread_metadata")
    return thread.thread_metadata.archive_timestamp


def _user_id(member: ThreadMember) -> int:
    if member.user_id is None:
        raise _unpaged(member, "a thread member has no user_id")
    return member.user_id


def _unpaged(item: Model, detail: str) -> UnexpectedResponse:
    return UnexpectedResponse(item.raw_json or b"", detail, took_effect=False)
