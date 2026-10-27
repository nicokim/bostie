import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import msgspec
import pytest

from lyrecord import events
from lyrecord.enums import ApplicationFlags
from lyrecord.events import (
    EVENTS,
    ChannelUpdate,
    Event,
    GuildCreate,
    GuildDelete,
    GuildUpdate,
    InteractionCreate,
    MemberJoin,
    MemberRemove,
    MemberUpdate,
    MessageCreate,
    MessageDelete,
    RawEvent,
    ReactionAdd,
    Ready,
    ThreadCreate,
    ThreadListSync,
    VoiceServerUpdate,
    VoiceStateUpdate,
)
from lyrecord.models import Builder, Guild, Member, Model
from lyrecord.models.base import decode as decode_model
from lyrecord.models.base import decoder

EVENT_FIXTURES = Path(__file__).parent / "fixtures" / "models" / "events"
MODELLED = sorted(
    path for path in EVENT_FIXTURES.glob("*.json") if path.name.split(".")[0] in EVENTS
)


TOKEN = "lyrecord-test-token-7c1f"


def subclasses(base: type) -> Iterator[type]:
    subclass: type
    for subclass in base.__subclasses__():
        yield subclass
        yield from subclasses(subclass)


STRUCTS: list[type[msgspec.Struct]] = [
    *subclasses(Model),
    *subclasses(Builder),
    *subclasses(Event),
]
SECRET_KEYS = {
    field.encode_name
    for struct in STRUCTS
    for field in msgspec.structs.fields(struct)
    if "token" in field.encode_name or "secret" in field.encode_name
}


def decode[E: Event](event: type[E], name: str, shard_id: int = 0) -> E:
    return event.decode(shard_id, (EVENT_FIXTURES / f"{name}.json").read_bytes())


@pytest.mark.parametrize("path", MODELLED, ids=lambda path: path.stem)
def test_every_dispatch_decodes_to_its_event(path: Path) -> None:
    data = path.read_bytes()
    event_type = EVENTS[path.name.split(".")[0]]

    event = event_type.decode(3, data)

    assert event.shard_id == 3
    assert event.raw_json is data
    assert event.raw == json.loads(data)


@pytest.mark.parametrize(
    ("event_type", "name", "entity"),
    [
        (MessageCreate, "MESSAGE_CREATE", "message"),
        (ChannelUpdate, "CHANNEL_UPDATE", "channel"),
        (ThreadCreate, "THREAD_CREATE", "channel"),
        (InteractionCreate, "INTERACTION_CREATE.command", "interaction"),
        (VoiceStateUpdate, "VOICE_STATE_UPDATE", "voice_state"),
        (GuildUpdate, "GUILD_UPDATE", "guild"),
    ],
)
def test_an_event_that_is_one_entity_shares_its_payload_with_it(
    event_type: type[Event], name: str, entity: str
) -> None:
    event = decode(event_type, name)

    value: Model = getattr(event, entity)

    assert value.raw_json is event.raw_json


def test_guild_create_decodes_the_header_and_children_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoded: list[object] = []

    def counting_decoder(type_: Any) -> msgspec.json.Decoder[Any]:
        decoded.append(type_)
        return decoder(type_)

    def counting_decode(model: type[Model], data: bytes) -> Model:
        decoded.append(model)
        return decode_model(model, data)

    monkeypatch.setattr(events, "decoder", counting_decoder)
    monkeypatch.setattr(events, "decode", counting_decode)

    event = decode(GuildCreate, "GUILD_CREATE")

    assert len(decoded) == 1
    assert type(event.guild) is Guild
    assert event.guild.name == "lyrecord test"
    assert event.guild.member_count == 4
    assert event.guild.system_channel_id == 1335173295636611073
    assert event.guild.raw is None
    assert [channel.name for channel in event.channels] == [
        "General",
        "general",
        "Voice Lounge",
        "___hidden___",
        "help-forum",
    ]
    assert all(channel.guild_id is None for channel in event.channels)
    assert event.channels[3].obfuscated
    assert [thread.name for thread in event.threads] == ["release-notes-0.1"]
    assert [role.name for role in event.roles] == [
        "@everyone",
        "lyrecord-test",
        "Moderator",
        "Admin",
    ]
    assert event.voice_states[0].user_id == 308882807193731073


def test_guild_create_members_and_presences_decode_on_access() -> None:
    event = decode(GuildCreate, "GUILD_CREATE")

    members = event.members

    assert all(type(member) is Member for member in members)
    assert [member.display_name for member in members] == [
        "Ada",
        "Grace H.",
        "linus",
        "lyrecord-test",
    ]
    assert members[0].roles == (1335174050611331073,)
    assert [presence["status"] for presence in event.presences] == [
        "online",
        "idle",
        "online",
    ]


def test_unavailable_guild_create_is_only_a_marker() -> None:
    event = decode(GuildCreate, "GUILD_CREATE.unavailable")

    assert event.guild == Guild(id=1335172792320131073, unavailable=True)
    assert event.channels == ()
    assert event.members == ()


def test_guild_delete_reads_the_guild_id_from_id() -> None:
    outage = decode(GuildDelete, "GUILD_DELETE")
    left = decode(GuildDelete, "GUILD_DELETE.left")

    assert outage.guild_id == left.guild_id == 1335172792320131073
    assert outage.unavailable is True
    assert left.unavailable is None


@pytest.mark.parametrize(
    ("event_type", "name"),
    [(MemberJoin, "GUILD_MEMBER_ADD"), (MemberUpdate, "GUILD_MEMBER_UPDATE")],
)
def test_member_events_split_the_guild_id_from_the_member(
    event_type: type[MemberJoin | MemberUpdate], name: str
) -> None:
    event = decode(event_type, name)

    assert event.guild_id == 1335172792320131073
    assert type(event.member) is Member
    assert event.member.user is not None
    assert event.member.id == int(json.loads(event.raw_json)["user"]["id"])


def test_member_remove_carries_only_the_user() -> None:
    event = decode(MemberRemove, "GUILD_MEMBER_REMOVE")

    assert event.guild_id == 1335172792320131073
    assert event.user is not None
    assert event.user.username == "newcomer"


def test_ready() -> None:
    event = decode(Ready, "READY")

    assert event.user is not None
    assert event.user.bot is True
    assert [guild.id for guild in event.guilds] == [
        1335172792320131073,
        1335927767040131073,
    ]
    assert all(guild.unavailable for guild in event.guilds)
    assert event.application is not None
    assert event.application.flags == 9084928
    assert ApplicationFlags.APPLICATION_COMMAND_BADGE in event.application.flags
    assert event.session_id == "70374e9c7263b9d5e16c4f418ff85dc1"


def test_thread_list_sync_tells_channel_scope_from_guild_scope() -> None:
    channels = decode(ThreadListSync, "THREAD_LIST_SYNC")
    guild = decode(ThreadListSync, "THREAD_LIST_SYNC.guild")

    assert channels.channel_ids == (1335173295636611073,)
    assert guild.channel_ids is None
    assert [thread.parent_id for thread in guild.threads] == [
        1335173295636611073,
        1382046656102531073,
    ]


def test_message_and_reaction_events() -> None:
    created = decode(MessageCreate, "MESSAGE_CREATE")
    reply = decode(MessageCreate, "MESSAGE_CREATE.rich").message.referenced_message
    deleted = decode(MessageDelete, "MESSAGE_DELETE")
    reaction = decode(ReactionAdd, "MESSAGE_REACTION_ADD")

    assert created.message.guild_id == 1335172792320131073
    assert reply is not None
    assert reply.jump_url == (
        "https://discord.com/channels/1335172792320131073/"
        "1335173295636611073/1564609610244030465"
    )
    assert created.message.member is not None
    assert created.message.member.user is None
    assert deleted.id == created.message.id
    assert reaction.emoji is not None
    assert reaction.emoji.animated is True
    assert reaction.member is not None
    assert reaction.message_author_id == 1329027298099331073


def test_voice_events() -> None:
    state = decode(VoiceStateUpdate, "VOICE_STATE_UPDATE").voice_state
    server = decode(VoiceServerUpdate, "VOICE_SERVER_UPDATE")

    assert state.session_id == "70374e9c7263b9d5e16c4f418ff85dc1"
    assert state.self_deaf is True
    assert state.member is not None
    assert server.endpoint == "c-iad20-3a7f91c2.discord.media:443"
    assert server.token == "8f3e2b9c1d7a4f60"


@pytest.mark.parametrize("struct", STRUCTS, ids=lambda struct: struct.__qualname__)
def test_no_repr_shows_a_token_or_the_payload_holding_it(
    struct: type[msgspec.Struct],
) -> None:
    payload: dict[str, Any] = {"id": "1", "name": "n", "seq": 1}
    payload |= dict.fromkeys(SECRET_KEYS, TOKEN)
    if any(
        field.required and field.name == "type"
        for field in msgspec.structs.fields(struct)
    ):
        payload["type"] = 1
    data = msgspec.json.encode(payload)
    value: Any
    if issubclass(struct, Event):
        value = struct.decode(0, data)
    elif issubclass(struct, Model):
        value = decode_model(struct, data)
    else:
        value = decoder(struct).decode(data)

    for text in (repr(value), str(value), repr(value.__rich_repr__())):
        assert TOKEN not in text


def test_reprs_leave_out_interaction_and_voice_tokens() -> None:
    created = decode(InteractionCreate, "INTERACTION_CREATE.command")
    server = decode(VoiceServerUpdate, "VOICE_SERVER_UPDATE")
    raw = RawEvent(
        shard_id=0, raw_json=created.raw_json, name=InteractionCreate.NAME, seq=1
    )
    token = created.interaction.token
    assert token is not None and server.token is not None

    for value, secret in [
        (created, token),
        (created.interaction, token),
        (raw, token),
        (server, server.token),
    ]:
        assert secret not in repr(value)
        assert secret not in str(value)
    assert repr(raw) == "RawEvent(shard_id=0, name='INTERACTION_CREATE', seq=1)"
