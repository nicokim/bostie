import msgspec

from bostie.enums import (
    ActivityLocationKind,
    ApplicationEventWebhookStatus,
    ApplicationFlags,
    ApplicationIntegrationType,
    ApplicationRoleConnectionMetadataType,
    MembershipState,
    Permissions,
    TeamMemberRole,
    WebhookEventType,
)
from bostie.models.base import Builder, Model
from bostie.models.user import PartialUser, User


class PartialApplication(Model, kw_only=True):
    id: int
    _flags: int = msgspec.field(default=0, name="flags")
    _flags_new: str | None = msgspec.field(default=None, name="flags_new")

    @property
    def flags(self) -> ApplicationFlags:
        return ApplicationFlags(
            self._flags if self._flags_new is None else int(self._flags_new)
        )


class InstallParams(Builder, kw_only=True):
    """The scopes and bot permissions an install link asks for."""

    scopes: tuple[str, ...]
    permissions: Permissions


class ApplicationIntegrationTypeConfiguration(Builder, kw_only=True):
    oauth2_install_params: InstallParams | None = None


class TeamMember(Model, kw_only=True):
    membership_state: MembershipState
    team_id: int
    user: User
    role: TeamMemberRole


class Team(Model, kw_only=True):
    icon: str | None
    id: int
    members: tuple[TeamMember, ...]
    name: str
    owner_user_id: int


class Application(PartialApplication, kw_only=True):
    """The bot's app, with the settings its owner sees."""

    name: str
    icon: str | None
    description: str
    rpc_origins: tuple[str, ...] = ()
    bot_public: bool
    bot_require_code_grant: bool
    bot: PartialUser | None = None
    terms_of_service_url: str | None = None
    privacy_policy_url: str | None = None
    owner: PartialUser | None = None
    verify_key: str
    team: Team | None
    guild_id: int | None = None
    primary_sku_id: int | None = None
    slug: str | None = None
    cover_image: str | None = None
    approximate_guild_count: int | None = None
    approximate_user_install_count: int | None = None
    approximate_user_authorization_count: int | None = None
    redirect_uris: tuple[str, ...] = ()
    interactions_endpoint_url: str | None = None
    role_connections_verification_url: str | None = None
    event_webhooks_url: str | None = None
    event_webhooks_status: ApplicationEventWebhookStatus | None = None
    event_webhooks_types: tuple[WebhookEventType, ...] = ()
    tags: tuple[str, ...] = ()
    install_params: InstallParams | None = None
    integration_types_config: dict[
        ApplicationIntegrationType, ApplicationIntegrationTypeConfiguration
    ] = {}
    custom_install_url: str | None = None


class ActivityLocation(Model, kw_only=True):
    """The channel an Activity instance runs in."""

    id: str
    kind: ActivityLocationKind
    channel_id: int
    guild_id: int | None = None


class ActivityInstance(Model, kw_only=True):
    """A running Activity and the users connected to it."""

    application_id: int
    instance_id: str
    launch_id: int
    location: ActivityLocation
    users: tuple[int, ...]


class ApplicationRoleConnectionMetadata(Builder, kw_only=True):
    """A value a guild can require of a user's connection to the app for a role."""

    type: ApplicationRoleConnectionMetadataType
    key: str
    name: str
    name_localizations: dict[str, str] | None = None
    description: str
    description_localizations: dict[str, str] | None = None


class SessionStartLimit(Model, kw_only=True):
    """How many more gateway sessions the bot may start before `reset_after` ms."""

    total: int
    remaining: int
    reset_after: int
    max_concurrency: int


class GatewayInfo(Model, kw_only=True):
    url: str


class GatewayBotInfo(GatewayInfo, kw_only=True):
    """The gateway URL, the recommended shard count and the session start limit."""

    shards: int
    session_start_limit: SessionStartLimit
