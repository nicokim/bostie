import logging
from typing import Any, ClassVar, Self, dataclass_transform

import msgspec

from lyrecord.enums import Intents
from lyrecord.models import (
    Application,
    Channel,
    Guild,
    Interaction,
    Member,
    Message,
    Model,
    PartialEmoji,
    Role,
    UnavailableGuild,
    User,
    VoiceState,
)
from lyrecord.models.base import decode, decoder, redacted_repr, shown_fields
from lyrecord.models.message import in_guild

logger = logging.getLogger("lyrecord.events")

_undecodable: set[str] = set()


@dataclass_transform(
    field_specifiers=(msgspec.field,), kw_only_default=True, frozen_default=True
)
class Event(msgspec.Struct, kw_only=True, frozen=True, gc=False):
    """Base of the typed gateway events."""

    NAME: ClassVar[str]
    INTENTS: ClassVar[Intents] = Intents(0)

    shard_id: int = msgspec.field(default=0, name="__lyrecord_shard__")
    raw_json: bytes = msgspec.field(default=b"", name="__lyrecord_raw__")

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

    user: User | None = None
    guilds: tuple[UnavailableGuild, ...] = ()
    application: Application | None = None
    session_id: str | None = None


class Resumed(Event, kw_only=True):
    NAME = "RESUMED"


class _GuildCreatePayload(Guild, kw_only=True):
    channels: tuple[Channel, ...] = ()
    threads: tuple[Channel, ...] = ()
    roles: tuple[Role, ...] = ()
    voice_states: tuple[VoiceState, ...] = ()
    members_json: msgspec.Raw = msgspec.field(
        default=msgspec.Raw(b"[]"), name="members"
    )
    presences_json: msgspec.Raw = msgspec.field(
        default=msgspec.Raw(b"[]"), name="presences"
    )


class GuildCreate(Event, kw_only=True):
    """A guild became available or the bot joined it. Its children lack `guild_id`."""

    NAME = "GUILD_CREATE"
    INTENTS = Intents.GUILDS

    guild: Guild
    channels: tuple[Channel, ...] = ()
    threads: tuple[Channel, ...] = ()
    roles: tuple[Role, ...] = ()
    voice_states: tuple[VoiceState, ...] = ()
    members_json: msgspec.Raw = msgspec.Raw(b"[]")
    presences_json: msgspec.Raw = msgspec.Raw(b"[]")

    @property
    def members(self) -> tuple[Member, ...]:
        members: tuple[Member, ...] = decoder(tuple[Member, ...]).decode(
            self.members_json
        )
        return members

    @property
    def presences(self) -> list[dict[str, Any]]:
        presences: list[dict[str, Any]] = msgspec.json.decode(self.presences_json)
        return presences

    @classmethod
    def decode(cls, shard_id: int, data: bytes) -> Self:
        payload: _GuildCreatePayload = decoder(_GuildCreatePayload).decode(data)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            guild=_narrow(Guild, payload),
            channels=payload.channels,
            threads=payload.threads,
            roles=payload.roles,
            voice_states=payload.voice_states,
            members_json=payload.members_json,
            presences_json=payload.presences_json,
        )


class GuildUpdate(Event, kw_only=True):
    NAME = "GUILD_UPDATE"
    INTENTS = Intents.GUILDS

    guild: Guild

    @classmethod
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(shard_id=shard_id, raw_json=data, guild=decode(Guild, data))


class GuildDelete(Event, kw_only=True):
    """The bot left a guild, or the guild went unavailable if `unavailable` is true."""

    NAME = "GUILD_DELETE"
    INTENTS = Intents.GUILDS

    guild_id: int = msgspec.field(name="id")
    unavailable: bool | None = None


class _ChannelEvent(Event, kw_only=True):
    INTENTS = Intents.GUILDS

    channel: Channel

    @classmethod
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


class ThreadDelete(_ChannelEvent, kw_only=True):
    NAME = "THREAD_DELETE"


class ThreadListSync(Event, kw_only=True):
    """The active threads of some channels, or of the guild if `channel_ids` is None."""

    NAME = "THREAD_LIST_SYNC"
    INTENTS = Intents.GUILDS

    guild_id: int | None = None
    channel_ids: tuple[int, ...] | None = None
    threads: tuple[Channel, ...] = ()


class _RoleEvent(Event, kw_only=True):
    INTENTS = Intents.GUILDS

    guild_id: int | None = None
    role: Role | None = None


class RoleCreate(_RoleEvent, kw_only=True):
    NAME = "GUILD_ROLE_CREATE"


class RoleUpdate(_RoleEvent, kw_only=True):
    NAME = "GUILD_ROLE_UPDATE"


class RoleDelete(Event, kw_only=True):
    NAME = "GUILD_ROLE_DELETE"
    INTENTS = Intents.GUILDS

    guild_id: int | None = None
    role_id: int | None = None


class _MessageEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_MESSAGES | Intents.DIRECT_MESSAGES

    message: Message

    @classmethod
    def decode(cls, shard_id: int, data: bytes) -> Self:
        message = decode(Message, data)
        in_guild(message, message.guild_id)
        return cls(shard_id=shard_id, raw_json=data, message=message)


class MessageCreate(_MessageEvent, kw_only=True):
    NAME = "MESSAGE_CREATE"


class MessageUpdate(_MessageEvent, kw_only=True):
    NAME = "MESSAGE_UPDATE"


class MessageDelete(Event, kw_only=True):
    NAME = "MESSAGE_DELETE"
    INTENTS = Intents.GUILD_MESSAGES | Intents.DIRECT_MESSAGES

    id: int
    channel_id: int | None = None
    guild_id: int | None = None


class _ReactionEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_MESSAGE_REACTIONS | Intents.DIRECT_MESSAGE_REACTIONS

    user_id: int | None = None
    channel_id: int | None = None
    message_id: int | None = None
    guild_id: int | None = None
    member: Member | None = None
    emoji: PartialEmoji | None = None
    message_author_id: int | None = None
    burst: bool | None = None


class ReactionAdd(_ReactionEvent, kw_only=True):
    NAME = "MESSAGE_REACTION_ADD"


class ReactionRemove(_ReactionEvent, kw_only=True):
    NAME = "MESSAGE_REACTION_REMOVE"


class _GuildMemberPayload(Member, kw_only=True):
    guild_id: int | None = None


class _MemberEvent(Event, kw_only=True):
    INTENTS = Intents.GUILD_MEMBERS

    guild_id: int | None = None
    member: Member

    @classmethod
    def decode(cls, shard_id: int, data: bytes) -> Self:
        payload: _GuildMemberPayload = decoder(_GuildMemberPayload).decode(data)
        return cls(
            shard_id=shard_id,
            raw_json=data,
            guild_id=payload.guild_id,
            member=_narrow(Member, payload),
        )


class MemberJoin(_MemberEvent, kw_only=True):
    NAME = "GUILD_MEMBER_ADD"


class MemberUpdate(_MemberEvent, kw_only=True):
    NAME = "GUILD_MEMBER_UPDATE"


class MemberRemove(Event, kw_only=True):
    NAME = "GUILD_MEMBER_REMOVE"
    INTENTS = Intents.GUILD_MEMBERS

    guild_id: int | None = None
    user: User | None = None


class InteractionCreate(Event, kw_only=True):
    NAME = "INTERACTION_CREATE"

    interaction: Interaction

    @classmethod
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id, raw_json=data, interaction=decode(Interaction, data)
        )


class VoiceStateUpdate(Event, kw_only=True):
    NAME = "VOICE_STATE_UPDATE"
    INTENTS = Intents.GUILD_VOICE_STATES

    voice_state: VoiceState

    @classmethod
    def decode(cls, shard_id: int, data: bytes) -> Self:
        return cls(
            shard_id=shard_id, raw_json=data, voice_state=decode(VoiceState, data)
        )


class VoiceServerUpdate(Event, kw_only=True):
    NAME = "VOICE_SERVER_UPDATE"

    token: str | None = None
    guild_id: int | None = None
    endpoint: str | None = None


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
