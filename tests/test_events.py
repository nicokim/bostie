import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
import pytest

from bostie import Bot, events
from bostie.enums import ChannelType, MemberFlags, Status
from bostie.events import (
    EVENTS,
    AuditLogEntryCreate,
    AutoModerationRuleCreate,
    ChannelInfo,
    ChannelUpdate,
    CommandPermissionsUpdate,
    EntitlementCreate,
    Event,
    GatewayRateLimited,
    GuildCreate,
    GuildUpdate,
    IntegrationCreate,
    InteractionCreate,
    MemberJoin,
    MemberUpdate,
    MessageCreate,
    PresenceUpdate,
    RawEvent,
    ScheduledEventCreate,
    SoundboardSoundCreate,
    StageInstanceCreate,
    SubscriptionCreate,
    ThreadCreate,
    ThreadMemberUpdate,
    TypingStart,
    UserUpdate,
    VoiceChannelStartTimeUpdate,
    VoiceServerUpdate,
    VoiceStateUpdate,
    decode_event,
)
from bostie.models import (
    AuditLogEntry,
    CommandInteraction,
    Guild,
    Integration,
    Member,
    Message,
    Model,
    PartialEntitlement,
    PartialUser,
    Presence,
    ThreadMember,
    UnavailableGuild,
    User,
    WebhookMessage,
)
from bostie.testing import FakeDiscord, payloads, run, running

EVENT_FIXTURES = payloads.FIXTURES / "events"
MODELLED = sorted(EVENT_FIXTURES.glob("*.json"))


TOKEN = "bostie-test-token-7c1f"


def decode[E: Event](event: type[E], name: str, shard_id: int = 0) -> E:
    return event.decode(shard_id, (EVENT_FIXTURES / f"{name}.json").read_bytes())


def without(document: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in document.items() if name != key}


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
        (AutoModerationRuleCreate, "AUTO_MODERATION_RULE_CREATE", "rule"),
        (SoundboardSoundCreate, "GUILD_SOUNDBOARD_SOUND_CREATE", "sound"),
        (ScheduledEventCreate, "GUILD_SCHEDULED_EVENT_CREATE", "scheduled_event"),
        (EntitlementCreate, "ENTITLEMENT_CREATE", "entitlement"),
        (SubscriptionCreate, "SUBSCRIPTION_CREATE", "subscription"),
        (UserUpdate, "USER_UPDATE", "user"),
        (StageInstanceCreate, "STAGE_INSTANCE_CREATE", "stage_instance"),
        (
            CommandPermissionsUpdate,
            "APPLICATION_COMMAND_PERMISSIONS_UPDATE",
            "permissions",
        ),
    ],
)
def test_an_event_that_is_one_entity_shares_its_payload_with_it(
    event_type: type[Event], name: str, entity: str
) -> None:
    event = decode(event_type, name)

    value: Model = getattr(event, entity)

    assert value.raw_json is event.raw_json


def test_a_test_entitlement_arrives_without_a_period() -> None:
    event = decode(EntitlementCreate, "ENTITLEMENT_CREATE.test")

    assert type(event.entitlement) is PartialEntitlement


def test_guild_create_splits_the_guild_from_its_children() -> None:
    event = decode(GuildCreate, "GUILD_CREATE")

    assert type(event.guild) is Guild
    assert event.guild.name == "bostie test"
    assert event.guild.member_count == 4
    assert event.guild.joined_at == datetime(2025, 2, 1, 9, 30, 4, 221000, tzinfo=UTC)
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
    assert [role.name for role in event.guild.roles] == [
        "@everyone",
        "bostie-test",
        "Moderator",
        "Admin",
    ]
    assert event.voice_states[0].user_id == 308882807193731073
    assert [sound.name for sound in event.soundboard_sounds] == ["lyre strum"]
    assert [scheduled.name for scheduled in event.guild_scheduled_events] == [
        "Weekly jam"
    ]


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
    assert [presence.status for presence in event.presences] == [
        Status.ONLINE,
        Status.IDLE,
        Status.ONLINE,
    ]
    assert event.presences[1].activities[0].assets is not None


def test_unavailable_guild_create_is_only_a_marker() -> None:
    event = decode(GuildCreate, "GUILD_CREATE.unavailable")

    assert event.guild == UnavailableGuild(id=1335172792320131073, unavailable=True)
    assert event.channels == ()
    assert event.members == ()


def test_a_broken_guild_create_names_its_own_missing_field() -> None:
    document = json.loads((EVENT_FIXTURES / "GUILD_CREATE.json").read_bytes())
    del document["unavailable"]

    with pytest.raises(msgspec.ValidationError, match="`name`"):
        GuildCreate.decode(0, msgspec.json.encode(without(document, "name")))


@pytest.mark.parametrize(
    ("event_type", "name", "entity", "model"),
    [
        (MemberJoin, "GUILD_MEMBER_ADD", "member", Member),
        (AuditLogEntryCreate, "GUILD_AUDIT_LOG_ENTRY_CREATE", "entry", AuditLogEntry),
        (PresenceUpdate, "PRESENCE_UPDATE", "presence", Presence),
        (ThreadMemberUpdate, "THREAD_MEMBER_UPDATE", "thread_member", ThreadMember),
        (IntegrationCreate, "INTEGRATION_CREATE", "integration", Integration),
    ],
)
def test_an_entity_with_a_guild_id_is_split_from_it(
    event_type: type[Event], name: str, entity: str, model: type[Model]
) -> None:
    event: Any = decode(event_type, name)

    value = getattr(event, entity)

    assert event.guild_id == 1335172792320131073
    assert type(value) is model


def test_member_update_decodes_the_documented_fields_without_flags() -> None:
    document = json.loads((EVENT_FIXTURES / "GUILD_MEMBER_UPDATE.json").read_bytes())

    event = MemberUpdate.decode(0, msgspec.json.encode(without(document, "flags")))

    assert event.guild_id == 1335172792320131073
    assert event.user.username == "linus"
    assert event.nick == "linus (timed out)"
    assert event.communication_disabled_until == datetime(2026, 10, 27, 13, tzinfo=UTC)
    assert event.flags == MemberFlags(0)
    assert event.avatar_decoration_data is not None
    assert event.avatar_decoration_data.sku_id == 1144058844004233369
    assert event.collectibles is not None
    assert event.collectibles.nameplate is not None
    assert event.collectibles.nameplate.palette == "cobalt"


def test_thread_create_says_whether_it_is_new_or_the_bot_was_added() -> None:
    created = decode(ThreadCreate, "THREAD_CREATE")
    added = decode(ThreadCreate, "THREAD_CREATE.added")

    assert (created.newly_created, created.member) == (True, None)
    assert added.newly_created is False
    assert added.channel.type is ChannelType.PRIVATE_THREAD
    assert added.member is not None
    assert added.member.user_id == 1329027298099331073


def test_a_webhook_messages_author_needs_only_the_webhooks_identity() -> None:
    document = json.loads((EVENT_FIXTURES / "MESSAGE_CREATE.webhook.json").read_bytes())
    identity = {key: document["author"][key] for key in ("id", "username", "avatar")}

    webhook = MessageCreate.decode(
        0, msgspec.json.encode(document | {"author": identity})
    ).message
    user = decode(MessageCreate, "MESSAGE_CREATE").message

    assert (type(webhook), type(webhook.author)) == (WebhookMessage, PartialUser)
    assert webhook.author.display_name == "Release notes"
    assert (type(user), type(user.author)) == (Message, User)


def test_a_malformed_nested_message_fails_on_access_and_its_event_arrives() -> None:
    document = json.loads((EVENT_FIXTURES / "MESSAGE_CREATE.rich.json").read_bytes())
    del document["referenced_message"]["content"]

    event = decode_event(MessageCreate, 0, msgspec.json.encode(document))

    assert event is not None
    with pytest.raises(msgspec.ValidationError, match="content"):
        _ = event.message.referenced_message


def test_rate_limit_metadata_is_decoded_for_request_guild_members_only() -> None:
    document = json.loads((EVENT_FIXTURES / "RATE_LIMITED.json").read_bytes())
    other = document | {"opcode": 3, "meta": {"since": 0}}

    members = decode(GatewayRateLimited, "RATE_LIMITED")
    presence = GatewayRateLimited.decode(0, msgspec.json.encode(other))

    assert members.meta is not None
    assert (members.meta.guild_id, members.meta.nonce) == (
        1335172792320131073,
        "chunk-1",
    )
    assert presence.retry_after == members.retry_after
    assert presence.meta is None


def test_typing_and_voice_session_start_times_read_unix_seconds() -> None:
    started = datetime(2026, 10, 27, 9, tzinfo=UTC)

    typing = decode(TypingStart, "TYPING_START")
    update = decode(VoiceChannelStartTimeUpdate, "VOICE_CHANNEL_START_TIME_UPDATE")
    info = decode(ChannelInfo, "CHANNEL_INFO")

    assert typing.timestamp == datetime(2026, 10, 27, 12, tzinfo=UTC)
    assert update.voice_start_time == started
    assert [channel.voice_start_time for channel in info.channels] == [started, None]


def test_typed_listeners_get_each_fixture_and_raw_ones_a_broken_one_too(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(events, "_undecodable", set())
    cases = [
        (path.name.split(".")[0], json.loads(path.read_bytes()), True)
        for path in MODELLED
    ]
    message = json.loads((EVENT_FIXTURES / "MESSAGE_CREATE.json").read_bytes())
    cases.append(("MESSAGE_CREATE", message | {"mentions": "everyone"}, False))

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
