from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

import msgspec

from bostie.enums import Permissions
from bostie.models.base import Model, permissions_from
from bostie.models.user import User

if TYPE_CHECKING:
    from bostie.models.interaction import Resolved


class RoleColors(Model, kw_only=True):
    primary_color: int | None = None
    secondary_color: int | None = None
    tertiary_color: int | None = None


class Role(Model, kw_only=True):
    """A guild role."""

    __option_type__: ClassVar[int] = 8

    id: int
    name: str | None = None
    colors: RoleColors | None = None
    hoist: bool | None = None
    icon: str | None = None
    unicode_emoji: str | None = None
    position: int | None = None
    _permissions: str | None = msgspec.field(default=None, name="permissions")
    managed: bool | None = None
    mentionable: bool | None = None
    flags: int | None = None

    @property
    def permissions(self) -> Permissions | None:
        return permissions_from(self._permissions)

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "Role | None":
        return resolved.roles.get(id) if resolved.roles else None


class Member(Model, kw_only=True):
    """A user's membership in a guild; members attached to messages lack `user`."""

    __option_type__: ClassVar[int] = 6

    user: User | None = None
    nick: str | None = None
    avatar: str | None = None
    roles: tuple[int, ...] = ()
    joined_at: datetime | None = None
    premium_since: datetime | None = None
    deaf: bool | None = None
    mute: bool | None = None
    pending: bool | None = None
    _permissions: str | None = msgspec.field(default=None, name="permissions")
    communication_disabled_until: datetime | None = None
    flags: int | None = None

    @property
    def permissions(self) -> Permissions | None:
        """The member's permissions in the channel of an interaction."""
        return permissions_from(self._permissions)

    @property
    def id(self) -> int | None:
        return None if self.user is None else self.user.id

    @property
    def mention(self) -> str | None:
        return None if self.user is None else self.user.mention

    @property
    def display_name(self) -> str | None:
        return self.nick or (None if self.user is None else self.user.display_name)

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "Member | None":
        return resolved.member(id)


class Guild(Model, kw_only=True):
    """The header of a guild, without its channels, roles or members."""

    id: int
    name: str | None = None
    icon: str | None = None
    owner_id: int | None = None
    afk_channel_id: int | None = None
    verification_level: int | None = None
    features: tuple[str, ...] = ()
    member_count: int | None = None
    large: bool | None = None
    unavailable: bool | None = None
    system_channel_id: int | None = None
    rules_channel_id: int | None = None
    preferred_locale: str | None = None
    premium_tier: int | None = None
    description: str | None = None
    banner: str | None = None
    vanity_url_code: str | None = None
    nsfw_level: int | None = None


class UnavailableGuild(Model, kw_only=True):
    id: int
    unavailable: bool | None = None


class Emoji(Model, kw_only=True):
    id: int | None = None
    name: str | None = None
    animated: bool | None = None
    roles: tuple[int, ...] = ()
    require_colons: bool | None = None
    managed: bool | None = None
    available: bool | None = None


class VoiceState(Model, kw_only=True):
    """A user's voice connection state in a guild."""

    guild_id: int | None = None
    channel_id: int | None = None
    user_id: int | None = None
    member: Member | None = None
    session_id: str | None = None
    deaf: bool | None = None
    mute: bool | None = None
    self_deaf: bool | None = None
    self_mute: bool | None = None
    self_stream: bool | None = None
    self_video: bool | None = None
    suppress: bool | None = None
    request_to_speak_timestamp: datetime | None = None
