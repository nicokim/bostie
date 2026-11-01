import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
import pytest
from fakediscord import FakeDiscord, run, running

from bostie import Bot, events
from bostie.enums import ApplicationFlags, MemberFlags
from bostie.events import (
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
    TypingStart,
    VoiceServerUpdate,
    VoiceStateUpdate,
)
from bostie.models import CommandInteraction, Guild, Member, Model, UnavailableGuild
from bostie.models.base import decode as decode_model
from bostie.models.base import decoder

EVENT_FIXTURES = Path(__file__).parent / "fixtures" / "models" / "events"
MODELLED = sorted(
    path for path in EVENT_FIXTURES.glob("*.json") if path.name.split(".")[0] in EVENTS
)


TOKEN = "bostie-test-token-7c1f"


def decode[E: Event](event: type[E], name: str, shard_id: int = 0) -> E:
    return event.decode(shard_id, (EVENT_FIXTURES / f"{name}.json").read_bytes())


def without(document: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in document.items() if name != key}


def required_keys(event: type[Event], document: dict[str, Any]) -> list[str]:
    required = []
    for key in document:
        try:
            event.decode(0, msgspec.json.encode(without(document, key)))
        except msgspec.ValidationError:
            required.append(key)
    return required


@pytest.mark.parametrize("path", MODELLED, ids=lambda path: path.stem)
def test_every_dispatch_decodes_to_its_event(path: Path) -> None:
    data = path.read_bytes()
    event_type = EVENTS[path.name.split(".")[0]]

    event = event_type.decode(3, data)

    assert event.shard_id == 3
    assert event.raw_json is data
    assert event.raw == json.loads(data)


def test_every_event_fixture_has_an_entry_in_the_registry() -> None:
    names = {path.name.split(".")[0] for path in EVENT_FIXTURES.glob("*.json")}

    assert names - EVENTS.keys() == {"RATE_LIMITED"}


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
    assert event.guild.name == "bostie test"
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
        "bostie-test",
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
        "bostie-test",
    ]
    assert members[0].roles == (1335174050611331073,)
    assert [presence["status"] for presence in event.presences] == [
        "online",
        "idle",
        "online",
    ]


def test_unavailable_guild_create_is_only_a_marker() -> None:
    event = decode(GuildCreate, "GUILD_CREATE.unavailable")

    assert event.guild == UnavailableGuild(id=1335172792320131073, unavailable=True)
    assert event.channels == ()
    assert event.members == ()


def test_guild_delete_reads_the_guild_id_from_id() -> None:
    outage = decode(GuildDelete, "GUILD_DELETE")
    left = decode(GuildDelete, "GUILD_DELETE.left")

    assert outage.guild_id == left.guild_id == 1335172792320131073
    assert outage.unavailable is True
    assert left.unavailable is False


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
    assert event.member.id == int(json.loads(event.raw_json)["user"]["id"])


def test_member_remove_carries_only_the_user() -> None:
    event = decode(MemberRemove, "GUILD_MEMBER_REMOVE")

    assert event.guild_id == 1335172792320131073
    assert event.user.username == "newcomer"


def test_ready() -> None:
    event = decode(Ready, "READY")

    assert event.user.bot is True
    assert [guild.id for guild in event.guilds] == [
        1335172792320131073,
        1335927767040131073,
    ]
    assert all(guild.unavailable for guild in event.guilds)
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
    assert created.message.member.flags == MemberFlags.COMPLETED_ONBOARDING
    assert deleted.id == created.message.id
    assert reaction.emoji.animated is True
    assert reaction.member is not None
    assert reaction.message_author_id == 1329027298099331073


def test_typing_start_reads_the_unix_timestamp_and_the_member() -> None:
    event = decode(TypingStart, "TYPING_START")

    assert event.channel_id == 1335173295636611073
    assert event.guild_id == 1335172792320131073
    assert event.timestamp == datetime(2026, 10, 27, 12, tzinfo=UTC)
    assert event.member is not None
    assert event.member.id == event.user_id == 820774541721731073


def test_voice_events() -> None:
    state = decode(VoiceStateUpdate, "VOICE_STATE_UPDATE").voice_state
    server = decode(VoiceServerUpdate, "VOICE_SERVER_UPDATE")

    assert state.session_id == "70374e9c7263b9d5e16c4f418ff85dc1"
    assert state.self_deaf is True
    assert state.member is not None
    assert server.endpoint == "c-iad20-3a7f91c2.discord.media:443"
    assert server.token == "8f3e2b9c1d7a4f60"


def test_typed_listeners_get_each_fixture_and_raw_ones_get_broken_ones_too(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(events, "_undecodable", set())
    cases: list[tuple[str, dict[str, Any], bool]] = []
    for path in MODELLED:
        name = path.name.split(".")[0]
        document = json.loads(path.read_bytes())
        cases.append((name, document, True))
        cases += [
            (name, without(document, key), False)
            for key in required_keys(EVENTS[name], document)
        ]

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        typed: set[tuple[str, bytes]] = set()
        raw: dict[int, tuple[str, bytes]] = {}

        @bot.listen(*EVENTS.values())
        async def on_typed(event: Event) -> None:
            typed.add((event.NAME, event.raw_json))

        @bot.listen
        async def on_raw(event: RawEvent) -> None:
            raw[event.seq] = (event.name, event.raw_json)

        async with running(bot, fake):
            seqs = [await fake.dispatch(name, document) for name, document, _ in cases]
            end = asyncio.Task(
                bot.wait_for(RawEvent, check=lambda event: event.name == "END"),
                loop=asyncio.get_running_loop(),
                eager_start=True,
            )
            await fake.dispatch("END", {})
            await end

        assert [raw[seq] in typed for seq in seqs] == [ok for _, _, ok in cases]

    with caplog.at_level(logging.WARNING):
        run(scenario)

    broken = {name for name, _, ok in cases if not ok}
    skipped = [
        record.getMessage().split()[0]
        for record in caplog.records
        if record.name == "bostie.events"
    ]
    assert sorted(skipped) == sorted(broken)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_reprs_leave_out_interaction_and_voice_tokens() -> None:
    created = decode(InteractionCreate, "INTERACTION_CREATE.command")
    server = decode(VoiceServerUpdate, "VOICE_SERVER_UPDATE")
    raw = RawEvent(
        shard_id=0, raw_json=created.raw_json, name=InteractionCreate.NAME, seq=1
    )
    assert isinstance(created.interaction, CommandInteraction)
    token = created.interaction.token

    for value, secret in [
        (created, token),
        (created.interaction, token),
        (raw, token),
        (server, server.token),
    ]:
        assert secret not in repr(value)
        assert secret not in str(value)
    assert repr(raw) == "RawEvent(shard_id=0, name='INTERACTION_CREATE', seq=1)"
