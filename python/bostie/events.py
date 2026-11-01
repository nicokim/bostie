import logging
from datetime import datetime
from typing import Any, ClassVar, Self, dataclass_transform, override

import msgspec

from bostie.enums import ChannelType, Intents, MemberFlags
from bostie.models import (
    Channel,
    Guild,
    Interaction,
    Member,
    Message,
    Model,
    PartialApplication,
    PartialEmoji,
    Role,
    UnavailableGuild,
    User,
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
    _members: msgspec.Raw = msgspec.Raw(b"[]")
    _presences: msgspec.Raw = msgspec.Raw(b"[]")

    @property
    def members(self) -> tuple[Member, ...]:
        members: tuple[Member, ...] = decoder(tuple[Member, ...]).decode(self._members)
        return members

    @property
    def presences(self) -> list[dict[str, Any]]:
        presences: list[dict[str, Any]] = msgspec.json.decode(self._presences)
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


class _ReactionEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_MESSAGE_REACTIONS | Intents.DIRECT_MESSAGE_REACTIONS

    user_id: int
    channel_id: int
    message_id: int
    guild_id: int | None = None
    emoji: PartialEmoji
    burst: bool


class ReactionAdd(_ReactionEvent, kw_only=True):
    NAME = "MESSAGE_REACTION_ADD"

    member: Member | None = None
    message_author_id: int | None = None


class ReactionRemove(_ReactionEvent, kw_only=True):
    NAME = "MESSAGE_REACTION_REMOVE"


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


class VoiceServerUpdate(Event, kw_only=True):
    NAME = "VOICE_SERVER_UPDATE"

    token: str
    guild_id: int
    endpoint: str | None


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
