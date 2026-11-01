from typing import Any

from bostie.enums import (
    AuditLogEvent,
    AutoModerationActionType,
    AutoModerationEventType,
    AutoModerationTriggerType,
    KeywordPresetType,
)
from bostie.models.base import Builder, Model
from bostie.models.channel import Channel
from bostie.models.guild import PartialIntegration
from bostie.models.interaction import ApplicationCommand
from bostie.models.scheduled_event import ScheduledEvent
from bostie.models.user import User
from bostie.models.webhook import Webhook


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


class AutoModerationRule(Model, kw_only=True):
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
    """A changed field of the target; a value Discord leaves out is None."""

    key: str
    new_value: Any = None
    old_value: Any = None


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


class AuditLogEntry(Model, kw_only=True):
    id: int
    action_type: AuditLogEvent
    target_id: str | None
    user_id: int | None
    changes: tuple[AuditLogChange, ...] = ()
    options: AuditLogEntryInfo | None = None
    reason: str | None = None


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
