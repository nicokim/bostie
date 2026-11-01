from datetime import datetime
from typing import TYPE_CHECKING

import msgspec

from bostie.enums import (
    MemberFlags,
    NSFWLevel,
    Permissions,
    PremiumTier,
    RoleFlags,
    VerificationLevel,
)
from bostie.models.base import Model, permissions_from
from bostie.models.user import User

if TYPE_CHECKING:
    from bostie.models.interaction import Resolved


class RoleColors(Model, kw_only=True):
    primary_color: int
    secondary_color: int | None
    tertiary_color: int | None


class Role(Model, kw_only=True):
    """A guild role."""

    id: int
    name: str
    colors: RoleColors
    hoist: bool
    icon: str | None = None
    unicode_emoji: str | None = None
    position: int
    _permissions: str = msgspec.field(name="permissions")
    managed: bool
    mentionable: bool
    flags: RoleFlags

    @property
    def permissions(self) -> Permissions:
        return Permissions(int(self._permissions))

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "Role | None":
        return resolved.roles.get(id)


class PartialMember(Model, kw_only=True):
    """A member without its user, as resolved options and message events carry it."""

    roles: tuple[int, ...]
    joined_at: datetime | None
    flags: MemberFlags
    nick: str | None = None
    avatar: str | None = None
    premium_since: datetime | None = None
    deaf: bool | None = None
    mute: bool | None = None
    pending: bool = False
    _permissions: str | None = msgspec.field(default=None, name="permissions")
    communication_disabled_until: datetime | None = None

    @property
    def permissions(self) -> Permissions | None:
        """The member's permissions in the channel of an interaction."""
        return permissions_from(self._permissions)


class Member(PartialMember, kw_only=True):
    """A user's membership in a guild."""

    user: User

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def mention(self) -> str:
        return self.user.mention

    @property
    def display_name(self) -> str:
        return self.nick or self.user.display_name

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "Member | None":
        return resolved.member(id)


class Guild(Model, kw_only=True):
    """The header of a guild, without its channels, roles or members."""

    id: int
    name: str
    icon: str | None
    owner_id: int
    afk_channel_id: int | None
    verification_level: VerificationLevel
    features: tuple[str, ...]
    system_channel_id: int | None
    rules_channel_id: int | None
    preferred_locale: str
    premium_tier: PremiumTier
    description: str | None
    banner: str | None
    vanity_url_code: str | None
    nsfw_level: NSFWLevel
    member_count: int | None = None
    large: bool | None = None
    unavailable: bool = False


class UnavailableGuild(Model, kw_only=True):
    """A guild Discord has not sent yet, or one it lost to an outage."""

    id: int
    unavailable: bool


class Emoji(Model, kw_only=True):
    id: int | None
    name: str | None
    roles: tuple[int, ...] = ()
    require_colons: bool | None = None
    managed: bool = False
    animated: bool = False
    available: bool | None = None


class VoiceState(Model, kw_only=True):
    """A user's voice connection state in a guild."""

    guild_id: int | None = None
    channel_id: int | None
    user_id: int
    member: Member | None = None
    session_id: str
    deaf: bool
    mute: bool
    self_deaf: bool
    self_mute: bool
    self_stream: bool = False
    self_video: bool
    suppress: bool
    request_to_speak_timestamp: datetime | None
