import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar, Self, dataclass_transform, override

import msgspec

from bostie.enums import (
    AnimationType,
    AutoModerationTriggerType,
    ChannelType,
    Intents,
    InviteTargetType,
    MemberFlags,
    ReactionType,
)
from bostie.models import (
    AddedThreadMember,
    AuditLogEntry,
    AutoModerationAction,
    AutoModerationRule,
    AvatarDecorationData,
    Channel,
    Collectibles,
    CurrentUser,
    Emoji,
    Entitlement,
    Guild,
    GuildCommandPermissions,
    Integration,
    Interaction,
    Member,
    Message,
    Model,
    PartialApplication,
    PartialEmoji,
    Presence,
    RequestGuildMembersRateLimit,
    Role,
    ScheduledEvent,
    SoundboardSound,
    StageInstance,
    Sticker,
    Subscription,
    ThreadMember,
    UnavailableGuild,
    User,
    VoiceChannelInfo,
    VoiceState,
    WebhookMessage,
)
from bostie.models.base import decode, decoder, redacted_repr, shown_fields
from bostie.models.interaction import decode_interaction
from bostie.models.message import decode_message

logger = logging.getLogger("bostie.events")

REQUEST_GUILD_MEMBERS = 8

_undecodable: set[str] = set()


@dataclass_transform(
    field_specifiers=(msgspec.field,), kw_only_default=True, frozen_default=True
)
class Event(msgspec.Struct, kw_only=True, frozen=True, gc=False):
    """Base of the typed gateway events.

    `INTENTS` are the intents Discord needs to send the event; the bot warns when a
    privileged one is missing.
    """

    NAME: ClassVar[str]
    INTENTS: ClassVar[Intents] = Intents(0)

    shard_id: int = msgspec.field(default=0, name="__bostie_shard__")
    raw_json: bytes = msgspec.field(default=b"", name="__bostie_raw__")

    __repr__ = redacted_repr
    __rich_repr__ = shown_fields

    @property
    def raw(self) -> Any:
        """The event's payload as a new dict."""
        return msgspec.json.decode(self.raw_json)

    @classmethod
    def decode(cls, shard_id: int, data: bytes) -> Self:
        """Decode the `d` payload of a dispatch."""
        event: Self = decoder(cls).decode(data)
        msgspec.structs.force_setattr(event, "shard_id", shard_id)
        msgspec.structs.force_setattr(event, "raw_json", data)
        return event


class Ready(Event, kw_only=True):
    """A shard started a session; its guilds then arrive as GuildCreate events."""

    NAME = "READY"

    user: CurrentUser
    guilds: tuple[UnavailableGuild, ...]
    application: PartialApplication
    session_id: str
    shard: tuple[int, int] | None = None


class Resumed(Event, kw_only=True):
    NAME = "RESUMED"


class _Availability(msgspec.Struct):
    unavailable: bool = False


class _GuildCreatePayload(Guild, kw_only=True):
    channels: tuple[Channel, ...]
    threads: tuple[Channel, ...]
    voice_states: tuple[VoiceState, ...]
    stage_instances: tuple[StageInstance, ...]
    guild_scheduled_events: tuple[ScheduledEvent, ...]
    soundboard_sounds: tuple[SoundboardSound, ...]
    _members: msgspec.Raw = msgspec.field(name="members")
    _presences: msgspec.Raw = msgspec.field(name="presences")


class GuildCreate(Event, kw_only=True):
    """A guild arrived: joined if `joined`, else available; children lack guild_id."""

    NAME = "GUILD_CREATE"
    INTENTS = Intents.GUILDS

    guild: Guild | UnavailableGuild
    joined: bool = False
    channels: tuple[Channel, ...] = ()
    threads: tuple[Channel, ...] = ()
    voice_states: tuple[VoiceState, ...] = ()
    stage_instances: tuple[StageInstance, ...] = ()
    guild_scheduled_events: tuple[ScheduledEvent, ...] = ()
    soundboard_sounds: tuple[SoundboardSound, ...] = ()
    _members: msgspec.Raw = msgspec.Raw(b"[]")
    _presences: msgspec.Raw = msgspec.Raw(b"[]")

    @property
    def members(self) -> tuple[Member, ...]:
        members: tuple[Member, ...] = decoder(tuple[Member, ...]).decode(self._members)
        return members

    @property
    def presences(self) -> tuple[Presence, ...]:
        presences: tuple[Presence, ...] = decoder(tuple[Presence, ...]).decode(
            self._presences
        )
        return presences

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        try:
            payload: _GuildCreatePayload = decoder(_GuildCreatePayload).decode(data)
        except msgspec.ValidationError:
            if not decoder(_Availability).decode(data).unavailable:
                raise
            unavailable: UnavailableGuild = decoder(UnavailableGuild).decode(data)
            return cls(shard_id=shard_id, raw_json=data, guild=unavailable)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            guild=_narrow(Guild, payload),
            channels=payload.channels,
            threads=payload.threads,
            voice_states=payload.voice_states,
            stage_instances=payload.stage_instances,
            guild_scheduled_events=payload.guild_scheduled_events,
            soundboard_sounds=payload.soundboard_sounds,
            _members=payload._members,
            _presences=payload._presences,
        )


class GuildUpdate(Event, kw_only=True):
    """A guild changed; `old` is the cached guild before the change."""

    NAME = "GUILD_UPDATE"
    INTENTS = Intents.GUILDS

    guild: Guild
    old: Guild | None = None

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(shard_id=shard_id, raw_json=data, guild=decode(Guild, data))


class GuildDelete(Event, kw_only=True):
    """The bot left a guild, or it went unavailable; `guild` is the cached one."""

    NAME = "GUILD_DELETE"
    INTENTS = Intents.GUILDS

    guild_id: int = msgspec.field(name="id")
    unavailable: bool = False
    guild: Guild | None = None


class _ChannelEvent(Event, kw_only=True):
    INTENTS = Intents.GUILDS

    channel: Channel

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(shard_id=shard_id, raw_json=data, channel=decode(Channel, data))


class ChannelCreate(_ChannelEvent, kw_only=True):
    NAME = "CHANNEL_CREATE"


class ChannelUpdate(_ChannelEvent, kw_only=True):
    """A channel changed; `old` is the cached channel before the change."""

    NAME = "CHANNEL_UPDATE"

    old: Channel | None = None


class ChannelDelete(_ChannelEvent, kw_only=True):
    NAME = "CHANNEL_DELETE"


class ChannelPinsUpdate(Event, kw_only=True):
    """A message was pinned or unpinned; not sent when a pinned message is deleted."""

    NAME = "CHANNEL_PINS_UPDATE"
    INTENTS = Intents.GUILDS | Intents.DIRECT_MESSAGES

    guild_id: int | None = None
    channel_id: int
    last_pin_timestamp: datetime | None = None


class _ThreadCreateFields(msgspec.Struct):
    newly_created: bool = False
    member: ThreadMember | None = None


class ThreadCreate(_ChannelEvent, kw_only=True):
    """A new thread, or one the bot was added to; `member` is then the bot's."""

    NAME = "THREAD_CREATE"

    newly_created: bool = False
    member: ThreadMember | None = None

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        fields: _ThreadCreateFields = decoder(_ThreadCreateFields).decode(data)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            channel=decode(Channel, data),
            newly_created=fields.newly_created,
            member=fields.member,
        )


class ThreadUpdate(_ChannelEvent, kw_only=True):
    """A thread changed; `old` is the cached thread before the change."""

    NAME = "THREAD_UPDATE"

    old: Channel | None = None


class ThreadDelete(Event, kw_only=True):
    """A thread was deleted; `thread` is the cached one."""

    NAME = "THREAD_DELETE"
    INTENTS = Intents.GUILDS

    id: int
    guild_id: int
    parent_id: int | None
    type: ChannelType
    thread: Channel | None = None


class ThreadListSync(Event, kw_only=True):
    """The active threads of some channels, or of the guild if `channel_ids` is None."""

    NAME = "THREAD_LIST_SYNC"
    INTENTS = Intents.GUILDS

    guild_id: int
    channel_ids: tuple[int, ...] | None = None
    threads: tuple[Channel, ...]
    members: tuple[ThreadMember, ...]


class _RoleEvent(Event, kw_only=True):
    INTENTS = Intents.GUILDS

    guild_id: int
    role: Role


class RoleCreate(_RoleEvent, kw_only=True):
    NAME = "GUILD_ROLE_CREATE"


class RoleUpdate(_RoleEvent, kw_only=True):
    """A role changed; `old` is the cached role before the change."""

    NAME = "GUILD_ROLE_UPDATE"

    old: Role | None = None


class RoleDelete(Event, kw_only=True):
    """A role was deleted; `role` is the cached one."""

    NAME = "GUILD_ROLE_DELETE"
    INTENTS = Intents.GUILDS

    guild_id: int
    role_id: int
    role: Role | None = None


class _MessageEvent(Event, kw_only=True):
    """Content fields need Intents.MESSAGE_CONTENT, except in DMs and mentions."""

    INTENTS = Intents.GUILD_MESSAGES | Intents.DIRECT_MESSAGES

    message: Message | WebhookMessage

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(shard_id=shard_id, raw_json=data, message=decode_message(data))


class MessageCreate(_MessageEvent, kw_only=True):
    NAME = "MESSAGE_CREATE"


class MessageUpdate(_MessageEvent, kw_only=True):
    """A message was edited; `old` is the cached one, with Bot(max_messages=...)."""

    NAME = "MESSAGE_UPDATE"

    old: Message | WebhookMessage | None = None


class _MessageDeletePayload(msgspec.Struct):
    id: int
    channel_id: int
    guild_id: int | None = None


class MessageDelete(Event, kw_only=True):
    """A message was deleted; `message` is the cached one if any."""

    NAME = "MESSAGE_DELETE"
    INTENTS = Intents.GUILD_MESSAGES | Intents.DIRECT_MESSAGES

    id: int
    channel_id: int
    guild_id: int | None = None
    message: Message | WebhookMessage | None = None

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        payload: _MessageDeletePayload = decoder(_MessageDeletePayload).decode(data)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            id=payload.id,
            channel_id=payload.channel_id,
            guild_id=payload.guild_id,
        )


class _MessageDeleteBulkPayload(msgspec.Struct):
    ids: tuple[int, ...]
    channel_id: int
    guild_id: int | None = None


class MessageDeleteBulk(Event, kw_only=True):
    """Messages were deleted; `messages` are those the message cache held."""

    NAME = "MESSAGE_DELETE_BULK"
    INTENTS = Intents.GUILD_MESSAGES

    ids: tuple[int, ...]
    channel_id: int
    guild_id: int | None = None
    messages: tuple[Message | WebhookMessage, ...] = ()

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        payload: _MessageDeleteBulkPayload = decoder(_MessageDeleteBulkPayload).decode(
            data
        )
        return cls(
            shard_id=shard_id,
            raw_json=data,
            ids=payload.ids,
            channel_id=payload.channel_id,
            guild_id=payload.guild_id,
        )


class _ReactionEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_MESSAGE_REACTIONS | Intents.DIRECT_MESSAGE_REACTIONS

    user_id: int
    channel_id: int
    message_id: int
    guild_id: int | None = None
    emoji: PartialEmoji
    burst: bool
    type: ReactionType


class ReactionAdd(_ReactionEvent, kw_only=True):
    NAME = "MESSAGE_REACTION_ADD"

    member: Member | None = None
    message_author_id: int | None = None
    burst_colors: tuple[str, ...] = ()


class ReactionRemove(_ReactionEvent, kw_only=True):
    NAME = "MESSAGE_REACTION_REMOVE"


class ReactionRemoveAll(Event, kw_only=True):
    NAME = "MESSAGE_REACTION_REMOVE_ALL"
    INTENTS = Intents.GUILD_MESSAGE_REACTIONS | Intents.DIRECT_MESSAGE_REACTIONS

    channel_id: int
    message_id: int
    guild_id: int | None = None


class ReactionRemoveEmoji(Event, kw_only=True):
    NAME = "MESSAGE_REACTION_REMOVE_EMOJI"
    INTENTS = Intents.GUILD_MESSAGE_REACTIONS | Intents.DIRECT_MESSAGE_REACTIONS

    channel_id: int
    guild_id: int | None = None
    message_id: int
    emoji: PartialEmoji


class _PollVoteEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_MESSAGE_POLLS | Intents.DIRECT_MESSAGE_POLLS

    user_id: int
    channel_id: int
    message_id: int
    guild_id: int | None = None
    answer_id: int


class PollVoteAdd(_PollVoteEvent, kw_only=True):
    NAME = "MESSAGE_POLL_VOTE_ADD"


class PollVoteRemove(_PollVoteEvent, kw_only=True):
    NAME = "MESSAGE_POLL_VOTE_REMOVE"


class TypingStart(Event, kw_only=True):
    """A user started typing; `member` and `guild_id` are None in DMs."""

    NAME = "TYPING_START"
    INTENTS = Intents.GUILD_MESSAGE_TYPING | Intents.DIRECT_MESSAGE_TYPING

    channel_id: int
    guild_id: int | None = None
    user_id: int
    timestamp: datetime
    member: Member | None = None


class _GuildMemberPayload(Member, kw_only=True):
    guild_id: int


class MemberJoin(Event, kw_only=True):
    NAME = "GUILD_MEMBER_ADD"
    INTENTS = Intents.GUILD_MEMBERS

    guild_id: int
    member: Member

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        payload: _GuildMemberPayload = decoder(_GuildMemberPayload).decode(data)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            guild_id=payload.guild_id,
            member=_narrow(Member, payload),
        )


class MemberUpdate(Event, kw_only=True):
    """A member changed; without GUILD_MEMBERS only the bot's own updates arrive."""

    NAME = "GUILD_MEMBER_UPDATE"
    INTENTS = Intents.GUILD_MEMBERS

    guild_id: int
    roles: tuple[int, ...]
    user: User
    nick: str | None = None
    avatar: str | None
    banner: str | None
    joined_at: datetime | None
    premium_since: datetime | None = None
    deaf: bool | None = None
    mute: bool | None = None
    pending: bool = False
    communication_disabled_until: datetime | None = None
    flags: MemberFlags = MemberFlags(0)
    avatar_decoration_data: AvatarDecorationData | None = None
    collectibles: Collectibles | None = None


class MemberRemove(Event, kw_only=True):
    NAME = "GUILD_MEMBER_REMOVE"
    INTENTS = Intents.GUILD_MEMBERS

    guild_id: int
    user: User


class InteractionCreate(Event, kw_only=True):
    NAME = "INTERACTION_CREATE"

    interaction: Interaction

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id, raw_json=data, interaction=decode_interaction(data)
        )


class VoiceStateUpdate(Event, kw_only=True):
    """A user joined, left or changed voice; `old` is their cached voice state."""

    NAME = "VOICE_STATE_UPDATE"
    INTENTS = Intents.GUILD_VOICE_STATES

    voice_state: VoiceState
    old: VoiceState | None = None

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id, raw_json=data, voice_state=decode(VoiceState, data)
        )


class VoiceChannelEffectSend(Event, kw_only=True):
    """Someone sent an emoji reaction or a soundboard sound in the bot's channel."""

    NAME = "VOICE_CHANNEL_EFFECT_SEND"
    INTENTS = Intents.GUILD_VOICE_STATES

    channel_id: int
    guild_id: int
    user_id: int
    emoji: Emoji | None = None
    animation_type: AnimationType | None = None
    animation_id: int | None = None
    sound_id: int | None = None
    sound_volume: float | None = None


class VoiceChannelStatusUpdate(Event, kw_only=True):
    NAME = "VOICE_CHANNEL_STATUS_UPDATE"
    INTENTS = Intents.GUILDS

    id: int
    guild_id: int
    status: str | None


class VoiceChannelStartTimeUpdate(Event, kw_only=True):
    NAME = "VOICE_CHANNEL_START_TIME_UPDATE"
    INTENTS = Intents.GUILDS

    id: int
    guild_id: int
    voice_start_time: datetime | None = None


class ChannelInfo(Event, kw_only=True):
    """The voice data of a guild's channels, sent for a Request Channel Info."""

    NAME = "CHANNEL_INFO"

    guild_id: int
    channels: tuple[VoiceChannelInfo, ...]


class VoiceServerUpdate(Event, kw_only=True):
    NAME = "VOICE_SERVER_UPDATE"

    token: str
    guild_id: int
    endpoint: str | None


class _AutoModerationRuleEvent(Event, kw_only=True):
    INTENTS = Intents.AUTO_MODERATION_CONFIGURATION

    rule: AutoModerationRule

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id, raw_json=data, rule=decode(AutoModerationRule, data)
        )


class AutoModerationRuleCreate(_AutoModerationRuleEvent, kw_only=True):
    NAME = "AUTO_MODERATION_RULE_CREATE"


class AutoModerationRuleUpdate(_AutoModerationRuleEvent, kw_only=True):
    NAME = "AUTO_MODERATION_RULE_UPDATE"


class AutoModerationRuleDelete(_AutoModerationRuleEvent, kw_only=True):
    NAME = "AUTO_MODERATION_RULE_DELETE"


class AutoModerationActionExecution(Event, kw_only=True):
    """A rule acted on a user's content; `content` needs the MESSAGE_CONTENT intent."""

    NAME = "AUTO_MODERATION_ACTION_EXECUTION"
    INTENTS = Intents.AUTO_MODERATION_EXECUTION

    guild_id: int
    action: AutoModerationAction
    rule_id: int
    rule_trigger_type: AutoModerationTriggerType
    user_id: int
    channel_id: int | None = None
    message_id: int | None = None
    alert_system_message_id: int | None = None
    content: str
    matched_keyword: str | None
    matched_content: str | None


class _AuditLogEntryPayload(AuditLogEntry, kw_only=True):
    guild_id: int


class AuditLogEntryCreate(Event, kw_only=True):
    NAME = "GUILD_AUDIT_LOG_ENTRY_CREATE"
    INTENTS = Intents.GUILD_MODERATION

    guild_id: int
    entry: AuditLogEntry

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        payload: _AuditLogEntryPayload = decoder(_AuditLogEntryPayload).decode(data)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            guild_id=payload.guild_id,
            entry=_narrow(AuditLogEntry, payload),
        )


class _BanEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_MODERATION

    guild_id: int
    user: User


class BanAdd(_BanEvent, kw_only=True):
    NAME = "GUILD_BAN_ADD"


class BanRemove(_BanEvent, kw_only=True):
    NAME = "GUILD_BAN_REMOVE"


class EmojisUpdate(Event, kw_only=True):
    """A guild's emojis changed; `old` are those of the cached guild."""

    NAME = "GUILD_EMOJIS_UPDATE"
    INTENTS = Intents.GUILD_EXPRESSIONS

    guild_id: int
    emojis: tuple[Emoji, ...]
    old: tuple[Emoji, ...] | None = None


class StickersUpdate(Event, kw_only=True):
    """A guild's stickers changed; `old` are those of the cached guild."""

    NAME = "GUILD_STICKERS_UPDATE"
    INTENTS = Intents.GUILD_EXPRESSIONS

    guild_id: int
    stickers: tuple[Sticker, ...]
    old: tuple[Sticker, ...] | None = None


class _SoundboardSoundEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_EXPRESSIONS

    sound: SoundboardSound

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id, raw_json=data, sound=decode(SoundboardSound, data)
        )


class SoundboardSoundCreate(_SoundboardSoundEvent, kw_only=True):
    NAME = "GUILD_SOUNDBOARD_SOUND_CREATE"


class SoundboardSoundUpdate(_SoundboardSoundEvent, kw_only=True):
    NAME = "GUILD_SOUNDBOARD_SOUND_UPDATE"


class SoundboardSoundDelete(Event, kw_only=True):
    NAME = "GUILD_SOUNDBOARD_SOUND_DELETE"
    INTENTS = Intents.GUILD_EXPRESSIONS

    sound_id: int
    guild_id: int


class SoundboardSoundsUpdate(Event, kw_only=True):
    NAME = "GUILD_SOUNDBOARD_SOUNDS_UPDATE"
    INTENTS = Intents.GUILD_EXPRESSIONS

    soundboard_sounds: tuple[SoundboardSound, ...]
    guild_id: int


class SoundboardSounds(Event, kw_only=True):
    """A guild's soundboard sounds, sent for a Request Soundboard Sounds."""

    NAME = "SOUNDBOARD_SOUNDS"

    soundboard_sounds: tuple[SoundboardSound, ...]
    guild_id: int


class _ScheduledEventEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_SCHEDULED_EVENTS

    scheduled_event: ScheduledEvent

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id,
            raw_json=data,
            scheduled_event=decode(ScheduledEvent, data),
        )


class ScheduledEventCreate(_ScheduledEventEvent, kw_only=True):
    NAME = "GUILD_SCHEDULED_EVENT_CREATE"


class ScheduledEventUpdate(_ScheduledEventEvent, kw_only=True):
    NAME = "GUILD_SCHEDULED_EVENT_UPDATE"


class ScheduledEventDelete(_ScheduledEventEvent, kw_only=True):
    NAME = "GUILD_SCHEDULED_EVENT_DELETE"


class _ScheduledEventUserEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_SCHEDULED_EVENTS

    guild_scheduled_event_id: int
    user_id: int
    guild_id: int


class ScheduledEventUserAdd(_ScheduledEventUserEvent, kw_only=True):
    NAME = "GUILD_SCHEDULED_EVENT_USER_ADD"


class ScheduledEventUserRemove(_ScheduledEventUserEvent, kw_only=True):
    NAME = "GUILD_SCHEDULED_EVENT_USER_REMOVE"


class _EntitlementEvent(Event, kw_only=True):
    entitlement: Entitlement

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id, raw_json=data, entitlement=decode(Entitlement, data)
        )


class EntitlementCreate(_EntitlementEvent, kw_only=True):
    NAME = "ENTITLEMENT_CREATE"


class EntitlementUpdate(_EntitlementEvent, kw_only=True):
    """An entitlement changed; a subscription's one only when it ends."""

    NAME = "ENTITLEMENT_UPDATE"


class EntitlementDelete(_EntitlementEvent, kw_only=True):
    NAME = "ENTITLEMENT_DELETE"


class _SubscriptionEvent(Event, kw_only=True):
    subscription: Subscription

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id, raw_json=data, subscription=decode(Subscription, data)
        )


class SubscriptionCreate(_SubscriptionEvent, kw_only=True):
    NAME = "SUBSCRIPTION_CREATE"


class SubscriptionUpdate(_SubscriptionEvent, kw_only=True):
    NAME = "SUBSCRIPTION_UPDATE"


class SubscriptionDelete(_SubscriptionEvent, kw_only=True):
    NAME = "SUBSCRIPTION_DELETE"


class _ThreadMemberPayload(ThreadMember, kw_only=True):
    guild_id: int


class ThreadMemberUpdate(Event, kw_only=True):
    """The bot's own membership in a thread changed."""

    NAME = "THREAD_MEMBER_UPDATE"
    INTENTS = Intents.GUILDS

    guild_id: int
    thread_member: ThreadMember

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        payload: _ThreadMemberPayload = decoder(_ThreadMemberPayload).decode(data)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            guild_id=payload.guild_id,
            thread_member=_narrow(ThreadMember, payload),
        )


class ThreadMembersUpdate(Event, kw_only=True):
    """Members joined or left a thread; others than the bot need GUILD_MEMBERS."""

    NAME = "THREAD_MEMBERS_UPDATE"
    INTENTS = Intents.GUILDS | Intents.GUILD_MEMBERS

    id: int
    guild_id: int
    member_count: int
    added_members: tuple[AddedThreadMember, ...] = ()
    removed_member_ids: tuple[int, ...] = ()


class MembersChunk(Event, kw_only=True):
    """Members sent for a Request Guild Members."""

    NAME = "GUILD_MEMBERS_CHUNK"

    guild_id: int
    members: tuple[Member, ...]
    chunk_index: int
    chunk_count: int
    not_found: tuple[int, ...] = ()
    presences: tuple[Presence, ...] = ()
    nonce: str | None = None


class _PresencePayload(Presence, kw_only=True):
    guild_id: int


class PresenceUpdate(Event, kw_only=True):
    """A member's status, activities or user changed."""

    NAME = "PRESENCE_UPDATE"
    INTENTS = Intents.GUILD_PRESENCES

    guild_id: int
    presence: Presence

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        payload: _PresencePayload = decoder(_PresencePayload).decode(data)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            guild_id=payload.guild_id,
            presence=_narrow(Presence, payload),
        )


class UserUpdate(Event, kw_only=True):
    """The bot's user changed; `old` is the cached user before the change."""

    NAME = "USER_UPDATE"

    user: CurrentUser
    old: CurrentUser | None = None

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(shard_id=shard_id, raw_json=data, user=decode(CurrentUser, data))


class CommandPermissionsUpdate(Event, kw_only=True):
    NAME = "APPLICATION_COMMAND_PERMISSIONS_UPDATE"

    permissions: GuildCommandPermissions

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id,
            raw_json=data,
            permissions=decode(GuildCommandPermissions, data),
        )


class GuildIntegrationsUpdate(Event, kw_only=True):
    NAME = "GUILD_INTEGRATIONS_UPDATE"
    INTENTS = Intents.GUILD_INTEGRATIONS

    guild_id: int


class _IntegrationPayload(Integration, kw_only=True):
    guild_id: int


class _IntegrationEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_INTEGRATIONS

    guild_id: int
    integration: Integration

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        payload: _IntegrationPayload = decoder(_IntegrationPayload).decode(data)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            guild_id=payload.guild_id,
            integration=_narrow(Integration, payload),
        )


class IntegrationCreate(_IntegrationEvent, kw_only=True):
    NAME = "INTEGRATION_CREATE"


class IntegrationUpdate(_IntegrationEvent, kw_only=True):
    NAME = "INTEGRATION_UPDATE"


class IntegrationDelete(Event, kw_only=True):
    NAME = "INTEGRATION_DELETE"
    INTENTS = Intents.GUILD_INTEGRATIONS

    id: int
    guild_id: int
    application_id: int | None = None


class InviteCreate(Event, kw_only=True):
    NAME = "INVITE_CREATE"
    INTENTS = Intents.GUILD_INVITES

    channel_id: int
    code: str
    created_at: datetime
    guild_id: int | None = None
    inviter: User | None = None
    max_age: int
    max_uses: int
    target_type: InviteTargetType | None = None
    target_user: User | None = None
    target_application: PartialApplication | None = None
    temporary: bool
    uses: int
    expires_at: datetime | None
    role_ids: tuple[int, ...] = ()


class InviteDelete(Event, kw_only=True):
    NAME = "INVITE_DELETE"
    INTENTS = Intents.GUILD_INVITES

    channel_id: int
    guild_id: int | None = None
    code: str


class _StageInstanceEvent(Event, kw_only=True):
    INTENTS = Intents.GUILDS

    stage_instance: StageInstance

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id,
            raw_json=data,
            stage_instance=decode(StageInstance, data),
        )


class StageInstanceCreate(_StageInstanceEvent, kw_only=True):
    NAME = "STAGE_INSTANCE_CREATE"


class StageInstanceUpdate(_StageInstanceEvent, kw_only=True):
    NAME = "STAGE_INSTANCE_UPDATE"


class StageInstanceDelete(_StageInstanceEvent, kw_only=True):
    NAME = "STAGE_INSTANCE_DELETE"


class WebhooksUpdate(Event, kw_only=True):
    """A webhook of the channel was created, updated or deleted."""

    NAME = "WEBHOOKS_UPDATE"
    INTENTS = Intents.GUILD_WEBHOOKS

    guild_id: int
    channel_id: int


class _RateLimit(msgspec.Struct):
    opcode: int
    retry_after: float


class _RequestGuildMembersMeta(msgspec.Struct):
    meta: RequestGuildMembersRateLimit


class GatewayRateLimited(Event, kw_only=True):
    """A gateway command was rate limited; only Request Guild Members has `meta`."""

    NAME = "RATE_LIMITED"

    opcode: int
    retry_after: float
    meta: RequestGuildMembersRateLimit | None = None

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        limit: _RateLimit = decoder(_RateLimit).decode(data)
        meta: RequestGuildMembersRateLimit | None = None
        if limit.opcode == REQUEST_GUILD_MEMBERS:
            meta = decoder(_RequestGuildMembersMeta).decode(data).meta
        return cls(
            shard_id=shard_id,
            raw_json=data,
            opcode=limit.opcode,
            retry_after=limit.retry_after,
            meta=meta,
        )


class RawEvent(Event, kw_only=True):
    """Any dispatch, modelled or not, with its payload in `raw`."""

    NAME = "*"

    name: str
    seq: int


@dataclass_transform(kw_only_default=True, frozen_default=True)
class BotEvent(msgspec.Struct, kw_only=True, frozen=True):
    """Base of the events that come from the bot, not Discord: bostie's and yours.

    Subclass it for an event of your own and send it with `bot.dispatch()`; a
    subclass without a NAME is named `<module>.<qualname>`.
    """

    NAME: ClassVar[str]
    INTENTS: ClassVar[Intents] = Intents(0)

    @override
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "NAME" not in vars(cls):
            cls.NAME = f"{cls.__module__}.{cls.__qualname__}"


class Started(BotEvent, kw_only=True):
    """Every shard is ready, once per start(); wait_until_ready() returns now."""

    NAME = "bot.started"


class Stopping(BotEvent, kw_only=True):
    """close() began after Started; its listeners finish before the gateway closes."""

    NAME = "bot.stopping"


class ShardReady(BotEvent, kw_only=True):
    """A shard's new session is ready: the guilds its READY listed have arrived."""

    NAME = "shard.ready"

    shard_id: int


class ShardDisconnect(BotEvent, kw_only=True):
    """A shard lost its connection; it resumes the session if `resuming`."""

    NAME = "shard.disconnect"

    shard_id: int
    code: int | None
    reason: str
    resuming: bool


class ShardStop(BotEvent, kw_only=True):
    """A shard stopped for good: `close()` ran, or Discord ended it with `code`."""

    NAME = "shard.stop"

    shard_id: int
    code: int | None
    reason: str


class ShardBackpressure(BotEvent, kw_only=True):
    """A shard paused reading: `queued_bytes` of events wait for the event loop."""

    NAME = "shard.backpressure"

    shard_id: int
    queued_bytes: int


class ListenerError(BotEvent, kw_only=True):
    """A listener raised; without a ListenerError listener the error is logged."""

    NAME = "bot.listener_error"

    listener: Callable[..., Any]
    event: Any
    error: Exception


EVENTS: dict[str, type[Event]] = {
    event.NAME: event
    for event in (
        Ready,
        Resumed,
        GuildCreate,
        GuildUpdate,
        GuildDelete,
        ChannelCreate,
        ChannelUpdate,
        ChannelDelete,
        ThreadCreate,
        ThreadUpdate,
        ThreadDelete,
        ThreadListSync,
        RoleCreate,
        RoleUpdate,
        RoleDelete,
        MessageCreate,
        MessageUpdate,
        MessageDelete,
        ReactionAdd,
        ReactionRemove,
        TypingStart,
        MemberJoin,
        MemberUpdate,
        MemberRemove,
        InteractionCreate,
        VoiceStateUpdate,
        VoiceServerUpdate,
        AutoModerationRuleCreate,
        AutoModerationRuleUpdate,
        AutoModerationRuleDelete,
        AutoModerationActionExecution,
        AuditLogEntryCreate,
        BanAdd,
        BanRemove,
        EmojisUpdate,
        StickersUpdate,
        SoundboardSoundCreate,
        SoundboardSoundUpdate,
        SoundboardSoundDelete,
        SoundboardSoundsUpdate,
        SoundboardSounds,
        ScheduledEventCreate,
        ScheduledEventUpdate,
        ScheduledEventDelete,
        ScheduledEventUserAdd,
        ScheduledEventUserRemove,
        EntitlementCreate,
        EntitlementUpdate,
        EntitlementDelete,
        SubscriptionCreate,
        SubscriptionUpdate,
        SubscriptionDelete,
        ThreadMemberUpdate,
        ThreadMembersUpdate,
        MembersChunk,
        PresenceUpdate,
        UserUpdate,
        ChannelPinsUpdate,
        MessageDeleteBulk,
        ReactionRemoveAll,
        ReactionRemoveEmoji,
        PollVoteAdd,
        PollVoteRemove,
        VoiceChannelEffectSend,
        VoiceChannelStatusUpdate,
        VoiceChannelStartTimeUpdate,
        ChannelInfo,
        CommandPermissionsUpdate,
        GuildIntegrationsUpdate,
        IntegrationCreate,
        IntegrationUpdate,
        IntegrationDelete,
        InviteCreate,
        InviteDelete,
        StageInstanceCreate,
        StageInstanceUpdate,
        StageInstanceDelete,
        WebhooksUpdate,
        GatewayRateLimited,
    )
}


def decode_event[E: Event](event: type[E], shard_id: int, data: bytes) -> E | None:
    """Decode a dispatch, or return None and log a warning when its shape changed.

    The warning is logged once per event name; `RawEvent` listeners still run.
    """
    try:
        return event.decode(shard_id, data)
    except msgspec.ValidationError as error:
        level = logging.DEBUG if event.NAME in _undecodable else logging.WARNING
        _undecodable.add(event.NAME)
        logger.log(
            level,
            "%s has an unexpected shape (%s); typed listeners skip it",
            event.NAME,
            error,
        )
        return None


def _narrow[M: Model](model: type[M], value: M) -> M:
    return model(**{field: getattr(value, field) for field in model.__struct_fields__})
