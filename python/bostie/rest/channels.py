from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any, Required, TypedDict, Unpack

import msgspec

from bostie.enums import (
    ChannelFlags,
    ChannelType,
    ForumLayoutType,
    OverwriteType,
    Permissions,
    SortOrderType,
    VideoQualityMode,
)
from bostie.file import File
from bostie.models import (
    Channel,
    DefaultReaction,
    FollowedChannel,
    ForumTag,
    decode,
)
from bostie.models.base import decoder
from bostie.rest.base import Group, each, wire


class PermissionOverwrite(TypedDict, total=False):
    """A role's or member's overwrite to set; an absent or None set is empty."""

    id: Required[int]
    type: Required[OverwriteType]
    allow: Permissions | None
    deny: Permissions | None


class ChannelPosition(TypedDict, total=False):
    id: Required[int]
    position: int | None
    lock_permissions: bool | None
    parent_id: int | None


class ChannelCreate(TypedDict, total=False):
    name: Required[str]
    type: ChannelType | None
    topic: str | None
    bitrate: int | None
    user_limit: int | None
    rate_limit_per_user: int | None
    position: int | None
    permission_overwrites: Sequence[PermissionOverwrite] | None
    parent_id: int | None
    nsfw: bool | None
    rtc_region: str | None
    video_quality_mode: VideoQualityMode | None
    default_auto_archive_duration: int | None
    default_reaction_emoji: DefaultReaction | None
    available_tags: Sequence[ForumTag] | None
    default_sort_order: SortOrderType | None
    default_forum_layout: ForumLayoutType | None
    default_thread_rate_limit_per_user: int | None
    flags: ChannelFlags | None


class ChannelEdit(TypedDict, total=False):
    """The settings of a group DM, guild channel or thread to change."""

    name: str
    icon: File
    type: ChannelType
    position: int | None
    topic: str | None
    nsfw: bool | None
    rate_limit_per_user: int | None
    bitrate: int | None
    user_limit: int | None
    permission_overwrites: Sequence[PermissionOverwrite] | None
    parent_id: int | None
    rtc_region: str | None
    video_quality_mode: VideoQualityMode | None
    default_auto_archive_duration: int | None
    flags: ChannelFlags
    available_tags: Sequence[ForumTag]
    default_reaction_emoji: DefaultReaction | None
    default_thread_rate_limit_per_user: int
    default_sort_order: SortOrderType | None
    default_forum_layout: ForumLayoutType
    archived: bool
    auto_archive_duration: int
    locked: bool
    invitable: bool
    applied_tags: Sequence[int]


class ChannelEditPermissions(TypedDict, total=False):
    allow: Permissions | None
    deny: Permissions | None
    type: Required[OverwriteType]


class ChannelFetchPermissions(TypedDict, total=False):
    implicit: bool


class ChannelSetVoiceStatus(TypedDict, total=False):
    status: Required[str | None]


class ChannelFollow(TypedDict, total=False):
    webhook_channel_id: Required[int]


class ChannelAddRecipient(TypedDict, total=False):
    access_token: Required[str]
    nick: Required[str]


class ChannelCreateDm(TypedDict, total=False):
    recipient_id: Required[int]


class ChannelCreateGroupDm(TypedDict, total=False):
    access_tokens: Required[Sequence[str]]
    nicks: Required[Mapping[int, str]]


class _Permissions(msgspec.Struct):
    permissions: str


class Channels(Group):
    async def add_recipient(
        self, channel_id: int, user_id: int, /, **options: Unpack[ChannelAddRecipient]
    ) -> None:
        await self._http._request(
            "PUT",
            "/channels/{channel_id}/recipients/{user_id}",
            json=wire(options, ChannelAddRecipient),
            channel_id=channel_id,
            user_id=user_id,
        )

    async def create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ChannelCreate],
    ) -> Channel:
        return await self._http._decoded(
            partial(decode, Channel),
            "POST",
            "/guilds/{guild_id}/channels",
            json=_with_overwrites(wire(options, ChannelCreate)),
            reason=reason,
            guild_id=guild_id,
        )

    async def create_dm(self, **options: Unpack[ChannelCreateDm]) -> Channel:
        return await self._http._decoded(
            partial(decode, Channel),
            "POST",
            "/users/@me/channels",
            json=wire(options, ChannelCreateDm),
        )

    async def create_group_dm(self, **options: Unpack[ChannelCreateGroupDm]) -> Channel:
        return await self._http._decoded(
            partial(decode, Channel),
            "POST",
            "/users/@me/channels",
            json=wire(options, ChannelCreateGroupDm),
        )

    async def delete(self, channel_id: int, /, *, reason: str | None = None) -> Channel:
        return await self._http._decoded(
            partial(decode, Channel),
            "DELETE",
            "/channels/{channel_id}",
            reason=reason,
            channel_id=channel_id,
        )

    async def delete_permission(
        self, channel_id: int, overwrite_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http._request(
            "DELETE",
            "/channels/{channel_id}/permissions/{overwrite_id}",
            reason=reason,
            channel_id=channel_id,
            overwrite_id=overwrite_id,
        )

    async def edit(
        self,
        channel_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ChannelEdit],
    ) -> Channel:
        return await self._http._decoded(
            partial(decode, Channel),
            "PATCH",
            "/channels/{channel_id}",
            json=_with_overwrites(wire(options, ChannelEdit)),
            reason=reason,
            channel_id=channel_id,
        )

    async def edit_permissions(
        self,
        channel_id: int,
        overwrite_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ChannelEditPermissions],
    ) -> None:
        await self._http._request(
            "PUT",
            "/channels/{channel_id}/permissions/{overwrite_id}",
            json=wire(options, ChannelEditPermissions),
            reason=reason,
            channel_id=channel_id,
            overwrite_id=overwrite_id,
        )

    async def edit_positions(
        self,
        guild_id: int,
        /,
        positions: Sequence[ChannelPosition],
        *,
        reason: str | None = None,
    ) -> None:
        await self._http._request(
            "PATCH",
            "/guilds/{guild_id}/channels",
            json=[wire(position, ChannelPosition) for position in positions],
            reason=reason,
            guild_id=guild_id,
        )

    async def fetch(self, channel_id: int, /) -> Channel:
        return await self._http._decoded(
            partial(decode, Channel),
            "GET",
            "/channels/{channel_id}",
            channel_id=channel_id,
        )

    async def fetch_member_permissions(
        self,
        channel_id: int,
        user_id: int,
        /,
        **options: Unpack[ChannelFetchPermissions],
    ) -> Permissions:
        """The member's permissions in the channel, with its roles and overwrites."""
        return await self._http._decoded(
            _permissions,
            "GET",
            "/channels/{channel_id}/permissions/member/{user_id}",
            query=wire(options, ChannelFetchPermissions),
            channel_id=channel_id,
            user_id=user_id,
        )

    async def fetch_role_permissions(
        self,
        channel_id: int,
        role_id: int,
        /,
        **options: Unpack[ChannelFetchPermissions],
    ) -> Permissions:
        """The permissions of the role and @everyone in the channel."""
        return await self._http._decoded(
            _permissions,
            "GET",
            "/channels/{channel_id}/permissions/role/{role_id}",
            query=wire(options, ChannelFetchPermissions),
            channel_id=channel_id,
            role_id=role_id,
        )

    async def follow(
        self,
        channel_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ChannelFollow],
    ) -> FollowedChannel:
        return await self._http._decoded(
            partial(decode, FollowedChannel),
            "POST",
            "/channels/{channel_id}/followers",
            json=wire(options, ChannelFollow),
            reason=reason,
            channel_id=channel_id,
        )

    async def list(self, guild_id: int, /) -> tuple[Channel, ...]:
        return await self._http._decoded(
            each(partial(decode, Channel)),
            "GET",
            "/guilds/{guild_id}/channels",
            guild_id=guild_id,
        )

    async def remove_recipient(self, channel_id: int, user_id: int, /) -> None:
        await self._http._request(
            "DELETE",
            "/channels/{channel_id}/recipients/{user_id}",
            channel_id=channel_id,
            user_id=user_id,
        )

    async def set_voice_status(
        self,
        channel_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ChannelSetVoiceStatus],
    ) -> None:
        await self._http._request(
            "PUT",
            "/channels/{channel_id}/voice-status",
            json=wire(options, ChannelSetVoiceStatus),
            reason=reason,
            channel_id=channel_id,
        )

    async def trigger_typing_indicator(self, channel_id: int, /) -> None:
        await self._http._request(
            "POST", "/channels/{channel_id}/typing", channel_id=channel_id
        )


def _permissions(data: bytes) -> Permissions:
    answer: _Permissions = decoder(_Permissions).decode(data)
    return Permissions(int(answer.permissions))


def _with_overwrites(body: dict[str, Any]) -> dict[str, Any]:
    overwrites = body.get("permission_overwrites")
    if overwrites is not None:
        body["permission_overwrites"] = [
            wire(overwrite, PermissionOverwrite) for overwrite in overwrites
        ]
    return body
