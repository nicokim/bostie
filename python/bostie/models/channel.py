from datetime import datetime
from typing import TYPE_CHECKING

import msgspec

from bostie.enums import (
    ChannelFlags,
    ChannelType,
    ForumLayoutType,
    OverwriteType,
    Permissions,
    SortOrderType,
    StagePrivacyLevel,
    VideoQualityMode,
)
from bostie.models.base import Builder, Model, permissions_from
from bostie.models.guild import Member
from bostie.models.presence import Presence
from bostie.models.user import User

if TYPE_CHECKING:
    from bostie.models.message import Resolved


class Overwrite(Model, kw_only=True):
    """A permission overwrite for a role or a member."""

    id: int
    type: OverwriteType
    _allow: str = msgspec.field(name="allow")
    _deny: str = msgspec.field(name="deny")

    @property
    def allow(self) -> Permissions:
        return Permissions(int(self._allow))

    @property
    def deny(self) -> Permissions:
        return Permissions(int(self._deny))


class ThreadMetadata(Model, kw_only=True):
    archived: bool
    auto_archive_duration: int
    archive_timestamp: datetime
    locked: bool
    invitable: bool | None = None
    create_timestamp: datetime | None = None


class _ThreadMember(Model, kw_only=True):
    id: int | None = None
    user_id: int | None = None
    join_timestamp: datetime
    flags: int


class ThreadMember(_ThreadMember, kw_only=True):
    """A user's membership in a thread; `flags` are its notification settings."""

    member: Member | None = None


class AddedThreadMember(_ThreadMember, kw_only=True):
    """A thread member THREAD_MEMBERS_UPDATE adds, with its member and presence."""

    member: Member
    presence: Presence | None


class ForumTag(Builder, kw_only=True):
    """A tag for threads in a forum or media channel; only `name` is needed to send."""

    id: int | None = None
    name: str
    moderated: bool | None = None
    emoji_id: int | None = None
    emoji_name: str | None = None


class DefaultReaction(Builder, kw_only=True):
    """The emoji of a forum's reaction button, by custom emoji id or unicode name."""

    emoji_id: int | None = None
    emoji_name: str | None = None


class PartialChannel(Model, kw_only=True):
    """A channel as an interaction carries it, with the user's permissions in it."""

    id: int
    type: ChannelType
    guild_id: int | None = None
    position: int | None = None
    name: str | None = None
    topic: str | None = None
    nsfw: bool = False
    last_message_id: int | None = None
    rate_limit_per_user: int | None = None
    parent_id: int | None = None
    last_pin_timestamp: datetime | None = None
    thread_metadata: ThreadMetadata | None = None
    flags: ChannelFlags = ChannelFlags(0)
    _permissions: str | None = msgspec.field(default=None, name="permissions")
    _app_permissions: str | None = msgspec.field(default=None, name="app_permissions")

    @property
    def permissions(self) -> Permissions | None:
        """The user's permissions in a channel resolved for an interaction."""
        return permissions_from(self._permissions)

    @property
    def app_permissions(self) -> Permissions | None:
        """The bot's permissions in a channel resolved for an interaction."""
        return permissions_from(self._app_permissions)

    @property
    def obfuscated(self) -> bool:
        """Whether Discord hid this channel's details because the bot cannot view it."""
        return ChannelFlags.CHANNEL_OBFUSCATED in self.flags

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "PartialChannel | None":
        return resolved.channels.get(id)


class Channel(PartialChannel, kw_only=True):
    """Any channel, thread included."""

    permission_overwrites: tuple[Overwrite, ...] = ()
    bitrate: int | None = None
    user_limit: int | None = None
    recipients: tuple[User, ...] = ()
    icon: str | None = None
    owner_id: int | None = None
    application_id: int | None = None
    managed: bool = False
    rtc_region: str | None = None
    video_quality_mode: VideoQualityMode | None = None
    message_count: int | None = None
    member_count: int | None = None
    member: ThreadMember | None = None
    default_auto_archive_duration: int | None = None
    total_message_sent: int | None = None
    available_tags: tuple[ForumTag, ...] = ()
    applied_tags: tuple[int, ...] = ()
    default_reaction_emoji: DefaultReaction | None = None
    default_thread_rate_limit_per_user: int | None = None
    default_sort_order: SortOrderType | None = None
    default_forum_layout: ForumLayoutType | None = None


class ActiveThreads(Model, kw_only=True):
    """A guild's active threads and the bot's membership in those it joined."""

    threads: tuple[Channel, ...]
    members: tuple[ThreadMember, ...]


class FollowedChannel(Model, kw_only=True):
    """An announcement channel followed through the webhook `webhook_id`."""

    channel_id: int
    webhook_id: int


class VoiceChannelInfo(Model, kw_only=True):
    """A channel's voice status and the start of its voice session."""

    id: int
    status: str | None = None
    voice_start_time: datetime | None = None


class StageInstance(Model, kw_only=True):
    """A live stage."""

    id: int
    guild_id: int
    channel_id: int
    topic: str
    privacy_level: StagePrivacyLevel
    guild_scheduled_event_id: int | None
