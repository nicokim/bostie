from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from lyrecord.enums import ChannelFlags, ChannelType, Permissions
from lyrecord.models.base import Model

if TYPE_CHECKING:
    from lyrecord.models.interaction import Resolved


class Overwrite(Model, kw_only=True):
    """A permission overwrite for a role (type 0) or a member (type 1)."""

    id: int
    type: int | None = None
    allow: Permissions | None = None
    deny: Permissions | None = None


class ThreadMetadata(Model, kw_only=True):
    archived: bool | None = None
    auto_archive_duration: int | None = None
    archive_timestamp: datetime | None = None
    locked: bool | None = None
    invitable: bool | None = None
    create_timestamp: datetime | None = None


class Channel(Model, kw_only=True):
    """Any channel, thread included."""

    __option_type__: ClassVar[int] = 7

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
    permissions: Permissions | None = None
    app_permissions: Permissions | None = None

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
