from datetime import datetime
from typing import TYPE_CHECKING

import msgspec

from bostie.enums import ChannelFlags, ChannelType, OverwriteType, Permissions
from bostie.models.base import Model, permissions_from

if TYPE_CHECKING:
    from bostie.models.interaction import Resolved


class Overwrite(Model, kw_only=True):
    """A permission overwrite for a role or a member."""

    id: int
    type: OverwriteType | None = None
    _allow: str | None = msgspec.field(default=None, name="allow")
    _deny: str | None = msgspec.field(default=None, name="deny")

    @property
    def allow(self) -> Permissions | None:
        return permissions_from(self._allow)

    @property
    def deny(self) -> Permissions | None:
        return permissions_from(self._deny)


class ThreadMetadata(Model, kw_only=True):
    archived: bool | None = None
    auto_archive_duration: int | None = None
    archive_timestamp: datetime | None = None
    locked: bool | None = None
    invitable: bool | None = None
    create_timestamp: datetime | None = None


class Channel(Model, kw_only=True):
    """Any channel, thread included."""

    id: int
    type: ChannelType | None = None
    guild_id: int | None = None
    position: int | None = None
    permission_overwrites: tuple[Overwrite, ...] = ()
    name: str | None = None
    topic: str | None = None
    nsfw: bool | None = None
    last_message_id: int | None = None
    bitrate: int | None = None
    user_limit: int | None = None
    rate_limit_per_user: int | None = None
    parent_id: int | None = None
    owner_id: int | None = None
    last_pin_timestamp: datetime | None = None
    rtc_region: str | None = None
    message_count: int | None = None
    member_count: int | None = None
    thread_metadata: ThreadMetadata | None = None
    flags: ChannelFlags | None = None
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
        return bool((self.flags or 0) & ChannelFlags.CHANNEL_OBFUSCATED)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "Channel | None":
        return resolved.channels.get(id) if resolved.channels else None
