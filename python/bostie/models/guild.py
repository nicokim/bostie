from datetime import datetime
from typing import TYPE_CHECKING

import msgspec

from bostie.enums import (
    DefaultMessageNotificationLevel,
    ExplicitContentFilterLevel,
    GuildFeature,
    IntegrationExpireBehavior,
    IntegrationType,
    Locale,
    MemberFlags,
    MFALevel,
    NSFWLevel,
    Permissions,
    PremiumTier,
    RoleFlags,
    Status,
    SystemChannelFlags,
    VerificationLevel,
)
from bostie.models.base import Builder, Model, permissions_from
from bostie.models.expressions import Emoji, Sticker
from bostie.models.user import AvatarDecorationData, Collectibles, User

if TYPE_CHECKING:
    from bostie.models.message import Resolved


class RoleColors(Builder, kw_only=True):
    primary_color: int
    secondary_color: int | None
    tertiary_color: int | None


class RoleTags(Model, kw_only=True):
    """What a role belongs to: a bot, an integration, boosts or a subscription."""

    bot_id: int | None = None
    integration_id: int | None = None
    _premium_subscriber: msgspec.UnsetType | None = msgspec.field(
        default=msgspec.UNSET, name="premium_subscriber"
    )
    subscription_listing_id: int | None = None
    _available_for_purchase: msgspec.UnsetType | None = msgspec.field(
        default=msgspec.UNSET, name="available_for_purchase"
    )
    _guild_connections: msgspec.UnsetType | None = msgspec.field(
        default=msgspec.UNSET, name="guild_connections"
    )

    @property
    def premium_subscriber(self) -> bool:
        """Whether this is the guild's booster role."""
        return self._premium_subscriber is None

    @property
    def available_for_purchase(self) -> bool:
        return self._available_for_purchase is None

    @property
    def guild_connections(self) -> bool:
        """Whether this is a linked role."""
        return self._guild_connections is None


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
    tags: RoleTags | None = None
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
    banner: str | None = None
    premium_since: datetime | None = None
    deaf: bool | None = None
    mute: bool | None = None
    pending: bool = False
    _permissions: str | None = msgspec.field(default=None, name="permissions")
    communication_disabled_until: datetime | None = None
    avatar_decoration_data: AvatarDecorationData | None = None
    collectibles: Collectibles | None = None

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


def with_user(member: PartialMember, user: User) -> Member:
    return Member(user=user, **msgspec.structs.asdict(member))


class WelcomeScreenChannel(Builder, kw_only=True):
    channel_id: int
    description: str
    emoji_id: int | None
    emoji_name: str | None


class WelcomeScreen(Model, kw_only=True):
    description: str | None
    welcome_channels: tuple[WelcomeScreenChannel, ...]


class IncidentsData(Model, kw_only=True):
    invites_disabled_until: datetime | None
    dms_disabled_until: datetime | None
    dm_spam_detected_at: datetime | None = None
    raid_detected_at: datetime | None = None


class Guild(Model, kw_only=True):
    """A guild, without its channels or members."""

    id: int
    name: str
    icon: str | None
    splash: str | None
    discovery_splash: str | None
    owner_id: int
    afk_channel_id: int | None
    afk_timeout: int
    widget_enabled: bool = False
    widget_channel_id: int | None = None
    verification_level: VerificationLevel
    default_message_notifications: DefaultMessageNotificationLevel
    explicit_content_filter: ExplicitContentFilterLevel
    roles: tuple[Role, ...]
    emojis: tuple[Emoji, ...]
    features: tuple[GuildFeature, ...]
    mfa_level: MFALevel
    application_id: int | None
    system_channel_id: int | None
    system_channel_flags: SystemChannelFlags
    rules_channel_id: int | None
    max_presences: int | None = None
    max_members: int | None = None
    vanity_url_code: str | None
    description: str | None
    banner: str | None
    premium_tier: PremiumTier
    premium_subscription_count: int | None = None
    preferred_locale: Locale
    public_updates_channel_id: int | None
    max_video_channel_users: int | None = None
    max_stage_video_channel_users: int | None = None
    approximate_member_count: int | None = None
    approximate_presence_count: int | None = None
    welcome_screen: WelcomeScreen | None = None
    nsfw_level: NSFWLevel
    stickers: tuple[Sticker, ...] = ()
    premium_progress_bar_enabled: bool
    safety_alerts_channel_id: int | None
    incidents_data: IncidentsData | None
    joined_at: datetime | None = None
    member_count: int | None = None
    large: bool | None = None
    unavailable: bool = False


class InteractionGuild(Model, kw_only=True):
    """The part of a guild an interaction from it carries."""

    id: int
    locale: Locale | None = None
    features: tuple[GuildFeature, ...] = ()


class CurrentUserGuild(Model, kw_only=True):
    """A guild the bot is in, with the bot's permissions there."""

    id: int
    name: str
    icon: str | None
    banner: str | None
    owner: bool
    _permissions: str = msgspec.field(name="permissions")
    features: tuple[GuildFeature, ...]
    approximate_member_count: int | None = None
    approximate_presence_count: int | None = None

    @property
    def permissions(self) -> Permissions:
        return Permissions(int(self._permissions))


class GuildPreview(Model, kw_only=True):
    id: int
    name: str
    icon: str | None
    splash: str | None
    discovery_splash: str | None
    emojis: tuple[Emoji, ...]
    features: tuple[GuildFeature, ...]
    approximate_member_count: int
    approximate_presence_count: int
    description: str | None
    stickers: tuple[Sticker, ...]


class GuildWidgetSettings(Model, kw_only=True):
    enabled: bool
    channel_id: int | None


class GuildWidgetChannel(Model, kw_only=True):
    id: int
    name: str
    position: int


class GuildWidgetMember(Model, kw_only=True):
    """An online member as the widget shows it, with an anonymized id."""

    id: str
    username: str
    discriminator: str
    avatar: str | None
    status: Status
    avatar_url: str


class GuildWidget(Model, kw_only=True):
    id: int
    name: str
    instant_invite: str | None
    channels: tuple[GuildWidgetChannel, ...]
    members: tuple[GuildWidgetMember, ...]
    presence_count: int


class VanityURL(Model, kw_only=True):
    code: str | None
    uses: int


class Ban(Model, kw_only=True):
    reason: str | None
    user: User


class BulkBan(Model, kw_only=True):
    """The ids of the users a bulk ban banned, and of those it did not."""

    banned_users: tuple[int, ...]
    failed_users: tuple[int, ...]


class UnavailableGuild(Model, kw_only=True):
    """A guild Discord has not sent yet, or one it lost to an outage."""

    id: int
    unavailable: bool


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


class VoiceRegion(Model, kw_only=True):
    id: str
    name: str
    optimal: bool
    deprecated: bool
    custom: bool


class IntegrationAccount(Model, kw_only=True):
    id: str
    name: str


class IntegrationApplication(Model, kw_only=True):
    id: int
    name: str
    icon: str | None
    description: str
    bot: User | None = None


class Integration(Model, kw_only=True):
    """A guild integration; bot integrations leave the subscriber fields None."""

    id: int
    name: str
    type: IntegrationType
    enabled: bool
    syncing: bool | None = None
    role_id: int | None = None
    enable_emoticons: bool | None = None
    expire_behavior: IntegrationExpireBehavior | None = None
    expire_grace_period: int | None = None
    user: User | None = None
    account: IntegrationAccount
    synced_at: datetime | None = None
    subscriber_count: int | None = None
    revoked: bool | None = None
    application: IntegrationApplication | None = None
    scopes: tuple[str, ...] = ()


class PartialIntegration(Model, kw_only=True):
    """An integration as the audit log refers to it."""

    id: int
    name: str
    type: IntegrationType
    account: IntegrationAccount
    application_id: int | None = None


class RequestGuildMembersRateLimit(Model, kw_only=True):
    """The metadata of a rate limited Request Guild Members."""

    guild_id: int
    nonce: str | None = None
