from collections.abc import Callable
from typing import Any

import msgspec

from bostie.enums import (
    AuditLogEvent,
    AutoModerationActionType,
    AutoModerationEventType,
    AutoModerationTriggerType,
    KeywordPresetType,
    Permissions,
)
from bostie.models.base import Builder, Model, Snowflake
from bostie.models.channel import Channel, Overwrite, StageInstance, ThreadMetadata
from bostie.models.expressions import Emoji, SoundboardSound, Sticker
from bostie.models.guild import Guild, Integration, Member, PartialIntegration, Role
from bostie.models.interaction import ApplicationCommand, CommandPermission
from bostie.models.invite import InviteWithMetadata
from bostie.models.onboarding import GuildOnboarding, OnboardingPrompt
from bostie.models.scheduled_event import ScheduledEvent
from bostie.models.user import User
from bostie.models.webhook import Webhook

PERMISSION_KEYS = frozenset({"permissions", "allow", "deny"})


class AutoModerationTriggerMetadata(Builder, kw_only=True):
    """What a rule matches; only the fields of its trigger type are set."""

    keyword_filter: tuple[str, ...] = ()
    regex_patterns: tuple[str, ...] = ()
    presets: tuple[KeywordPresetType, ...] = ()
    allow_list: tuple[str, ...] = ()
    mention_total_limit: int | None = None
    mention_raid_protection_enabled: bool | None = None


class AutoModerationActionMetadata(Builder, kw_only=True):
    """The settings of an action; only the fields of its action type are set."""

    channel_id: int | None = None
    duration_seconds: int | None = None
    custom_message: str | None = None


class AutoModerationAction(Builder, kw_only=True):
    type: AutoModerationActionType
    metadata: AutoModerationActionMetadata | None = None


class AutoModerationRule(Model, Snowflake, kw_only=True):
    id: int
    guild_id: int
    name: str
    creator_id: int
    event_type: AutoModerationEventType
    trigger_type: AutoModerationTriggerType
    trigger_metadata: AutoModerationTriggerMetadata
    actions: tuple[AutoModerationAction, ...]
    enabled: bool
    exempt_roles: tuple[int, ...]
    exempt_channels: tuple[int, ...]


class AuditLogChange(Model, kw_only=True):
    """A changed field, typed as its object types it; an unknown key keeps its JSON."""

    key: str
    new_value: Any = None
    old_value: Any = None


class AuditLogRole(Model, Snowflake, kw_only=True):
    """A role a member got or lost, as a `$add` or `$remove` change lists it."""

    id: int
    name: str


class AuditLogEntryInfo(Model, kw_only=True):
    """Extra details of an entry; only the fields of its action type are set."""

    application_id: int | None = None
    auto_moderation_rule_name: str | None = None
    auto_moderation_rule_trigger_type: str | None = None
    channel_id: int | None = None
    count: str | None = None
    delete_member_days: str | None = None
    id: int | None = None
    members_removed: str | None = None
    message_id: int | None = None
    role_name: str | None = None
    type: str | None = None
    integration_type: str | None = None
    status: str | None = None


class AuditLogEntry(Model, Snowflake, kw_only=True):
    id: int
    action_type: AuditLogEvent
    target_id: str | None
    user_id: int | None
    changes: tuple[AuditLogChange, ...] = ()
    options: AuditLogEntryInfo | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        type_of = CHANGE_TYPES.get(self.action_type, _untyped)
        typed = tuple(_typed(change, type_of(change.key)) for change in self.changes)
        msgspec.structs.force_setattr(self, "changes", typed)


class AuditLogPage(Model, kw_only=True):
    """A page of entries, with the users, threads and other objects they refer to."""

    application_commands: tuple[ApplicationCommand, ...]
    audit_log_entries: tuple[AuditLogEntry, ...]
    auto_moderation_rules: tuple[AutoModerationRule, ...]
    guild_scheduled_events: tuple[ScheduledEvent, ...]
    integrations: tuple[PartialIntegration, ...]
    threads: tuple[Channel, ...]
    users: tuple[User, ...]
    webhooks: tuple[Webhook, ...]


def _field_types(*models: type[msgspec.Struct]) -> dict[str, Any]:
    return {
        field.encode_name: Permissions
        if field.encode_name in PERMISSION_KEYS
        else field.type
        for model in reversed(models)
        for field in msgspec.structs.fields(model)
    }


def _events(
    *events: AuditLogEvent, types: dict[str, Any]
) -> dict[AuditLogEvent, Callable[[str], Any]]:
    return dict.fromkeys(events, types.get)


def _command_permission(_key: str) -> Any:
    return CommandPermission


def _untyped(_key: str) -> Any:
    return None


E = AuditLogEvent
ROLES = tuple[AuditLogRole, ...]
CHANGE_TYPES: dict[AuditLogEvent, Callable[[str], Any]] = {
    **_events(E.GUILD_UPDATE, types=_field_types(Guild)),
    **_events(
        E.CHANNEL_CREATE,
        E.CHANNEL_UPDATE,
        E.CHANNEL_DELETE,
        types=_field_types(Channel),
    ),
    **_events(
        E.CHANNEL_OVERWRITE_CREATE,
        E.CHANNEL_OVERWRITE_UPDATE,
        E.CHANNEL_OVERWRITE_DELETE,
        types=_field_types(Overwrite),
    ),
    **_events(E.MEMBER_UPDATE, types=_field_types(Member)),
    **_events(E.MEMBER_ROLE_UPDATE, types={"$add": ROLES, "$remove": ROLES}),
    **_events(E.ROLE_CREATE, E.ROLE_UPDATE, E.ROLE_DELETE, types=_field_types(Role)),
    **_events(
        E.INVITE_CREATE,
        E.INVITE_UPDATE,
        E.INVITE_DELETE,
        types=_field_types(InviteWithMetadata) | {"channel_id": int},
    ),
    **_events(
        E.WEBHOOK_CREATE,
        E.WEBHOOK_UPDATE,
        E.WEBHOOK_DELETE,
        types=_field_types(Webhook) | {"avatar_hash": str | None},
    ),
    **_events(
        E.EMOJI_CREATE, E.EMOJI_UPDATE, E.EMOJI_DELETE, types=_field_types(Emoji)
    ),
    **_events(
        E.INTEGRATION_CREATE,
        E.INTEGRATION_UPDATE,
        E.INTEGRATION_DELETE,
        types=_field_types(Integration),
    ),
    **_events(
        E.STAGE_INSTANCE_CREATE,
        E.STAGE_INSTANCE_UPDATE,
        E.STAGE_INSTANCE_DELETE,
        types=_field_types(StageInstance),
    ),
    **_events(
        E.STICKER_CREATE,
        E.STICKER_UPDATE,
        E.STICKER_DELETE,
        types=_field_types(Sticker),
    ),
    **_events(
        E.GUILD_SCHEDULED_EVENT_CREATE,
        E.GUILD_SCHEDULED_EVENT_UPDATE,
        E.GUILD_SCHEDULED_EVENT_DELETE,
        types=_field_types(ScheduledEvent),
    ),
    **_events(
        E.THREAD_CREATE,
        E.THREAD_UPDATE,
        E.THREAD_DELETE,
        types=_field_types(ThreadMetadata, Channel),
    ),
    **_events(
        E.SOUNDBOARD_SOUND_CREATE,
        E.SOUNDBOARD_SOUND_UPDATE,
        E.SOUNDBOARD_SOUND_DELETE,
        types=_field_types(SoundboardSound),
    ),
    **_events(
        E.AUTO_MODERATION_RULE_CREATE,
        E.AUTO_MODERATION_RULE_UPDATE,
        E.AUTO_MODERATION_RULE_DELETE,
        types=_field_types(AutoModerationRule),
    ),
    **_events(
        E.ONBOARDING_PROMPT_CREATE,
        E.ONBOARDING_PROMPT_UPDATE,
        E.ONBOARDING_PROMPT_DELETE,
        types=_field_types(OnboardingPrompt),
    ),
    **_events(
        E.ONBOARDING_CREATE, E.ONBOARDING_UPDATE, types=_field_types(GuildOnboarding)
    ),
    E.APPLICATION_COMMAND_PERMISSION_UPDATE: _command_permission,
}


def _typed(change: AuditLogChange, kind: Any) -> AuditLogChange:
    if kind is None:
        return change
    return msgspec.structs.replace(
        change,
        new_value=_converted(change.new_value, kind),
        old_value=_converted(change.old_value, kind),
    )


def _converted(value: object, kind: Any) -> object:
    if value is None:
        return None
    try:
        return msgspec.convert(value, kind, strict=False)
    except msgspec.ValidationError:
        return value
