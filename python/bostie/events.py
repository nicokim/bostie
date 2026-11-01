import logging
from datetime import datetime
from typing import Any, ClassVar, Self, dataclass_transform, override

import msgspec

from bostie.enums import (
    AnimationType,
    AutoModerationTriggerType,
    ChannelType,
    Intents,
    MemberFlags,
    ReactionType,
)
from bostie.models import (
    AddedThreadMember,
    AuditLogEntry,
    AutoModerationAction,
    AutoModerationRule,
    Channel,
    Emoji,
    Entitlement,
    Guild,
    Interaction,
    Member,
    Message,
    Model,
    PartialApplication,
    PartialEmoji,
    Presence,
    Role,
    ScheduledEvent,
    SoundboardSound,
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
    NAME = "READY"

    user: User
    guilds: tuple[UnavailableGuild, ...]
    application: PartialApplication
    session_id: str


class Resumed(Event, kw_only=True):
    NAME = "RESUMED"


class _Availability(msgspec.Struct):
    unavailable: bool = False


class _GuildCreatePayload(Guild, kw_only=True):
    channels: tuple[Channel, ...]
    threads: tuple[Channel, ...]
    roles: tuple[Role, ...]
    voice_states: tuple[VoiceState, ...]
    guild_scheduled_events: tuple[ScheduledEvent, ...]
    soundboard_sounds: tuple[SoundboardSound, ...]
    _members: msgspec.Raw = msgspec.field(name="members")
    _presences: msgspec.Raw = msgspec.field(name="presences")


class GuildCreate(Event, kw_only=True):
    """A guild became available or the bot joined it. Its children lack `guild_id`."""

    NAME = "GUILD_CREATE"
    INTENTS = Intents.GUILDS

    guild: Guild | UnavailableGuild
    channels: tuple[Channel, ...] = ()
    threads: tuple[Channel, ...] = ()
    roles: tuple[Role, ...] = ()
    voice_states: tuple[VoiceState, ...] = ()
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
            roles=payload.roles,
            voice_states=payload.voice_states,
            guild_scheduled_events=payload.guild_scheduled_events,
            soundboard_sounds=payload.soundboard_sounds,
            _members=payload._members,
            _presences=payload._presences,
        )


class GuildUpdate(Event, kw_only=True):
    NAME = "GUILD_UPDATE"
    INTENTS = Intents.GUILDS

    guild: Guild

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(shard_id=shard_id, raw_json=data, guild=decode(Guild, data))


class GuildDelete(Event, kw_only=True):
    """The bot left a guild, or the guild went unavailable if `unavailable` is true."""

    NAME = "GUILD_DELETE"
    INTENTS = Intents.GUILDS

    guild_id: int = msgspec.field(name="id")
    unavailable: bool = False


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
    NAME = "CHANNEL_UPDATE"


class ChannelDelete(_ChannelEvent, kw_only=True):
    NAME = "CHANNEL_DELETE"


class ChannelPinsUpdate(Event, kw_only=True):
    """A message was pinned or unpinned; not sent when a pinned message is deleted."""

    NAME = "CHANNEL_PINS_UPDATE"
    INTENTS = Intents.GUILDS | Intents.DIRECT_MESSAGES

    guild_id: int | None = None
    channel_id: int
    last_pin_timestamp: datetime | None = None


class ThreadCreate(_ChannelEvent, kw_only=True):
    NAME = "THREAD_CREATE"


class ThreadUpdate(_ChannelEvent, kw_only=True):
    NAME = "THREAD_UPDATE"


class ThreadDelete(Event, kw_only=True):
    NAME = "THREAD_DELETE"
    INTENTS = Intents.GUILDS

    id: int
    guild_id: int
    parent_id: int | None
    type: ChannelType


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
    NAME = "GUILD_ROLE_UPDATE"


class RoleDelete(Event, kw_only=True):
    NAME = "GUILD_ROLE_DELETE"
    INTENTS = Intents.GUILDS

    guild_id: int
    role_id: int


class _MessageEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_MESSAGES | Intents.DIRECT_MESSAGES

    message: Message | WebhookMessage

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(shard_id=shard_id, raw_json=data, message=decode_message(data))


class MessageCreate(_MessageEvent, kw_only=True):
    NAME = "MESSAGE_CREATE"


class MessageUpdate(_MessageEvent, kw_only=True):
    NAME = "MESSAGE_UPDATE"


class MessageDelete(Event, kw_only=True):
    NAME = "MESSAGE_DELETE"
    INTENTS = Intents.GUILD_MESSAGES | Intents.DIRECT_MESSAGES

    id: int
    channel_id: int
    guild_id: int | None = None


class MessageDeleteBulk(Event, kw_only=True):
    NAME = "MESSAGE_DELETE_BULK"
    INTENTS = Intents.GUILD_MESSAGES

    ids: tuple[int, ...]
    channel_id: int
    guild_id: int | None = None


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
    """A member changed, or its user did."""

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
    NAME = "VOICE_STATE_UPDATE"
    INTENTS = Intents.GUILD_VOICE_STATES

    voice_state: VoiceState

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
    NAME = "GUILD_EMOJIS_UPDATE"
    INTENTS = Intents.GUILD_EXPRESSIONS

    guild_id: int
    emojis: tuple[Emoji, ...]


class StickersUpdate(Event, kw_only=True):
    NAME = "GUILD_STICKERS_UPDATE"
    INTENTS = Intents.GUILD_EXPRESSIONS

    guild_id: int
    stickers: tuple[Sticker, ...]


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
    """The bot's user changed."""

    NAME = "USER_UPDATE"

    user: User

    @classmethod
    @override
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(shard_id=shard_id, raw_json=data, user=decode(User, data))


class RawEvent(Event, kw_only=True):
    """Any dispatch, modelled or not, with its payload in `raw`."""

    NAME = "*"

    name: str
    seq: int


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
