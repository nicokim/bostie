from datetime import datetime

import msgspec

from bostie.enums import (
    GuildFeature,
    GuildInviteFlags,
    InviteTargetType,
    InviteType,
    NSFWLevel,
    TargetUsersJobStatus,
    VerificationLevel,
)
from bostie.models.application import PartialApplication
from bostie.models.base import Model, Snowflake, decode, decoder
from bostie.models.channel import PartialChannel
from bostie.models.guild import RoleColors
from bostie.models.scheduled_event import ScheduledEvent
from bostie.models.user import User


class InviteGuild(Model, Snowflake, kw_only=True):
    """The part of a guild an invite shows."""

    id: int
    name: str
    splash: str | None = None
    banner: str | None = None
    description: str | None = None
    icon: str | None = None
    features: tuple[GuildFeature, ...] = ()
    verification_level: VerificationLevel | None = None
    vanity_url_code: str | None = None
    nsfw_level: NSFWLevel | None = None
    premium_subscription_count: int | None = None


class InviteRole(Model, Snowflake, kw_only=True):
    """A role the user gets by accepting an invite."""

    id: int
    name: str
    position: int
    colors: RoleColors
    icon: str | None = None
    unicode_emoji: str | None = None


class Invite(Model, kw_only=True):
    """A code that adds a user to a guild or a group DM."""

    type: InviteType
    code: str
    guild: InviteGuild | None = None
    channel: PartialChannel | None
    inviter: User | None = None
    target_type: InviteTargetType | None = None
    target_user: User | None = None
    target_application: PartialApplication | None = None
    approximate_presence_count: int | None = None
    approximate_member_count: int | None = None
    expires_at: datetime | None
    guild_scheduled_event: ScheduledEvent | None = None
    flags: GuildInviteFlags = GuildInviteFlags(0)
    roles: tuple[InviteRole, ...] = ()


class InviteWithMetadata(Invite, kw_only=True):
    """An invite with how it has been used, as its managers see it."""

    uses: int
    max_uses: int
    max_age: int
    temporary: bool
    created_at: datetime


class TargetUsersJob(Model, kw_only=True):
    """How far Discord got adding the users of an uploaded CSV to an invite."""

    status: TargetUsersJobStatus
    total_users: int
    processed_users: int
    created_at: datetime | None
    completed_at: datetime | None
    error_message: str | None


class _InviteShape(msgspec.Struct, gc=False):
    created_at: datetime | None = None


def decode_invite(data: bytes) -> Invite | InviteWithMetadata:
    """Decode an invite, with its metadata when Discord sent it."""
    shape = decoder(_InviteShape).decode(data)
    return decode(Invite if shape.created_at is None else InviteWithMetadata, data)
