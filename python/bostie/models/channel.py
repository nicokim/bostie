from datetime import datetime
from typing import TYPE_CHECKING

import msgspec

from bostie.enums import (
    ChannelFlags,
    ChannelType,
    OverwriteType,
    Permissions,
    StagePrivacyLevel,
)
from bostie.models.base import Model, permissions_from
from bostie.models.guild import Member
from bostie.models.presence import Presence

if TYPE_CHECKING:
    from bostie.models.interaction import Resolved


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
    owner_id: int | None = None
    rtc_region: str | None = None
    message_count: int | None = None
    member_count: int | None = None


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


class VoiceChannelInfo(Model, kw_only=True):
    """A channel's voice status and the start of its voice session."""

    id: int
    status: str | None = None
    voice_start_time: datetime | None = None


class StageInstance(Model, kw_only=True):
    """A live stage; `discoverable_disabled` is deprecated."""

    id: int
    guild_id: int
    channel_id: int
    topic: str
    privacy_level: StagePrivacyLevel
    discoverable_disabled: bool
    guild_scheduled_event_id: int | None
