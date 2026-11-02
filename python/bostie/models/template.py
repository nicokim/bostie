from datetime import datetime

from bostie.enums import (
    DefaultMessageNotificationLevel,
    ExplicitContentFilterLevel,
    Locale,
    Permissions,
    SystemChannelFlags,
    VerificationLevel,
)
from bostie.models.base import Model
from bostie.models.channel import Channel
from bostie.models.guild import RoleColors
from bostie.models.user import User


class TemplateRole(Model, kw_only=True):
    """A role a template creates; its `id` is a placeholder counted from 0."""

    id: int
    name: str
    permissions: Permissions
    colors: RoleColors | None = None
    hoist: bool
    mentionable: bool
    icon: str | None = None
    unicode_emoji: str | None = None


class TemplateGuild(Model, kw_only=True):
    """The guild a template creates; its ids are placeholders counted from 0."""

    name: str
    description: str | None
    verification_level: VerificationLevel
    default_message_notifications: DefaultMessageNotificationLevel
    explicit_content_filter: ExplicitContentFilterLevel
    preferred_locale: Locale
    afk_timeout: int
    roles: tuple[TemplateRole, ...]
    channels: tuple[Channel, ...]
    afk_channel_id: int | None
    system_channel_id: int | None
    system_channel_flags: SystemChannelFlags
    icon_hash: str | None = None


class GuildTemplate(Model, kw_only=True):
    """A snapshot of a guild that new guilds can be created from."""

    code: str
    name: str
    description: str | None
    usage_count: int
    creator_id: int
    creator: User
    created_at: datetime
    updated_at: datetime
    source_guild_id: int
    serialized_source_guild: TemplateGuild
    is_dirty: bool | None
