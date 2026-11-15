from collections.abc import Callable
from typing import Any

import msgspec
import pytest

from bostie import ChannelType, Permissions, Status
from bostie.cache import Cache
from bostie.events import (
    EVENTS,
    EmojisUpdate,
    Event,
    MemberRemove,
    MemberUpdate,
    MessageDelete,
    MessageDeleteBulk,
    MessageUpdate,
    PresenceUpdate,
    StickersUpdate,
    ThreadDelete,
    ThreadUpdate,
    VoiceStateUpdate,
)
from bostie.testing import payloads

EVENT_FIXTURES = payloads.FIXTURES / "events"
GUILD = 1335172792320131073
GENERAL = 1335173295636611073
STAFF_ONLY = 1335175308902531073
HELP_FORUM = 1382046656102531073
RELEASE_NOTES_THREAD = 1564337818828931073
FORUM_THREAD = 1564617159475331073
MOD_REVIEW_THREAD = 1564005629952131073
ADMIN_ROLE = 1335174050611331073
BOT_ROLE = 1335180342067331073
OWNER = 308882807193731073
BOT_USER = msgspec.json.decode((EVENT_FIXTURES / "READY.json").read_bytes())["user"]
BOT = int(BOT_USER["id"])
MODERATOR = 820774541721731073
SUPPORT = 613374681415811073
NEWCOMER = 1562134550937731073


def event(fixture: str, **changes: Any) -> Event:
    name = fixture.split(".", maxsplit=1)[0]
    document = msgspec.json.decode((EVENT_FIXTURES / f"{fixture}.json").read_bytes())
    document.update(changes)
    return EVENTS[name].decode(0, msgspec.json.encode(document))


def applied[E: Event](cache: Cache, cls: type[E], fixture: str, **changes: Any) -> E:
    decoded = event(fixture, **changes)
    assert isinstance(decoded, cls)
    return cache.apply(decoded)


@pytest.fixture
def cache() -> Cache:
    cache = Cache()
    cache.apply(event("READY"))
    cache.apply(event("GUILD_CREATE"))
    return cache


def channel_ids(cache: Cache) -> set[int]:
    return {channel.id for channel in cache.guild_channels(GUILD)}


def test_guild_create_fills_guild_channels_threads_and_roles(cache: Cache) -> None:
    guild = cache.guild(GUILD)
    bot = cache.user(BOT)

    assert bot is not None
    assert bot.username == "bostie-test"
    assert guild is not None
    assert guild.name == "bostie test"
    assert cache.guilds() == [guild]
    assert len(cache.guild_channels(GUILD)) == 6
    assert all(channel.guild_id == GUILD for channel in cache.guild_channels(GUILD))
    assert cache.channel(RELEASE_NOTES_THREAD) is not None
    assert [role.name for role in cache.guild_roles(GUILD)] == [
        "@everyone",
        "bostie-test",
        "Moderator",
        "Admin",
    ]
    admin = cache.role(ADMIN_ROLE)
    assert admin is not None
    assert admin.permissions == 8


def test_cached_entities_hold_no_raw_payload(cache: Cache) -> None:
    cache.apply(event("GUILD_UPDATE"))
    cache.apply(event("CHANNEL_CREATE"))
    cache.apply(event("THREAD_CREATE"))
    cache.apply(event("GUILD_ROLE_CREATE"))

    cached = [
        cache.guild(GUILD),
        *cache.guild_channels(GUILD),
        *cache.guild_roles(GUILD),
    ]

    assert all(value is not None and value.raw_json is None for value in cached)


def test_an_unavailable_guild_create_never_replaces_the_cached_header(
    cache: Cache,
) -> None:
    cache.apply(event("GUILD_CREATE.unavailable"))

    guild = cache.guild(GUILD)
    assert guild is not None
    assert guild.name == "bostie test"
    assert guild.unavailable is True
    assert len(cache.guild_channels(GUILD)) == 6


def test_a_new_guild_create_replaces_the_guild_children(cache: Cache) -> None:
    cache.apply(event("CHANNEL_CREATE"))

    cache.apply(event("GUILD_CREATE"))

    assert cache.channel(1564614642892931073) is None
    assert len(cache.guild_channels(GUILD)) == 6


def test_guild_update_replaces_the_header_and_keeps_the_create_fields(
    cache: Cache,
) -> None:
    cache.apply(event("GUILD_UPDATE"))

    guild = cache.guild(GUILD)
    assert guild is not None
    assert guild.description == "Fixture guild for bostie"
    assert guild.verification_level == 2
    assert guild.member_count == 4
    assert guild.joined_at is not None


def test_guild_delete_for_an_outage_keeps_the_guild(cache: Cache) -> None:
    cache.apply(event("GUILD_DELETE"))

    guild = cache.guild(GUILD)
    assert guild is not None
    assert guild.unavailable is True
    assert cache.channel(GENERAL) is not None
    assert cache.role(ADMIN_ROLE) is not None


def test_guild_delete_after_leaving_removes_everything(cache: Cache) -> None:
    cache.apply(event("GUILD_DELETE.left"))

    assert cache.guilds() == []
    assert cache.guild_channels(GUILD) == []
    assert cache.channel(GENERAL) is None
    assert cache.channel(RELEASE_NOTES_THREAD) is None
    assert cache.role(ADMIN_ROLE) is None


def test_channel_create_update_and_delete(cache: Cache) -> None:
    cache.apply(event("CHANNEL_CREATE"))
    created = cache.channel(1564614642892931073)
    cache.apply(event("CHANNEL_UPDATE", id="1564614642892931073", topic="bots"))
    updated = cache.channel(1564614642892931073)
    cache.apply(event("CHANNEL_DELETE"))

    assert created is not None
    assert created.name == "bot-commands"
    assert updated is not None
    assert updated.topic == "bots"
    assert cache.channel(1564614642892931073) is None
    assert 1564614642892931073 not in channel_ids(cache)


def test_deleting_a_channel_drops_its_threads_but_not_its_children(
    cache: Cache,
) -> None:
    category = 1335173043978371073

    cache.apply(event("CHANNEL_DELETE", id=str(GENERAL)))
    cache.apply(event("CHANNEL_DELETE", id=str(category), type=4))

    assert cache.channel(RELEASE_NOTES_THREAD) is None
    assert cache.channel(STAFF_ONLY) is not None


def test_channels_outside_cached_guilds_are_not_stored(cache: Cache) -> None:
    cache.apply(event("CHANNEL_CREATE", guild_id="1"))
    cache.apply(event("CHANNEL_CREATE", guild_id=None))

    assert cache.channel(1564614642892931073) is None


def test_threads_leave_the_cache_once_archived_or_deleted(cache: Cache) -> None:
    active = cache.channel(RELEASE_NOTES_THREAD)
    cache.apply(event("THREAD_CREATE"))
    created = cache.channel(FORUM_THREAD)

    archived = applied(cache, ThreadUpdate, "THREAD_UPDATE")
    deleted = applied(cache, ThreadDelete, "THREAD_DELETE")

    assert created is not None
    assert created.parent_id == HELP_FORUM
    assert archived.old == active
    assert cache.channel(RELEASE_NOTES_THREAD) is None
    assert deleted.thread == created
    assert cache.channel(FORUM_THREAD) is None


def test_thread_list_sync_replaces_the_threads_of_the_listed_channels(
    cache: Cache,
) -> None:
    stale = 1564999999999999999
    cache.apply(event("THREAD_CREATE"))
    cache.apply(event("THREAD_CREATE", id=str(stale), parent_id=str(GENERAL)))

    cache.apply(event("THREAD_LIST_SYNC"))

    assert cache.channel(stale) is None
    assert cache.channel(MOD_REVIEW_THREAD) is not None
    assert cache.channel(RELEASE_NOTES_THREAD) is not None
    assert cache.channel(FORUM_THREAD) is not None


def test_thread_list_sync_without_channel_ids_replaces_every_thread(
    cache: Cache,
) -> None:
    cache.apply(event("THREAD_LIST_SYNC"))

    cache.apply(event("THREAD_LIST_SYNC.guild"))

    assert cache.channel(MOD_REVIEW_THREAD) is None
    assert cache.channel(RELEASE_NOTES_THREAD) is not None
    forum_thread = cache.channel(FORUM_THREAD)
    assert forum_thread is not None
    assert forum_thread.guild_id == GUILD
    assert cache.channel(GENERAL) is not None


def test_an_obfuscated_update_is_stored_as_received_and_drops_its_threads(
    cache: Cache,
) -> None:
    cache.apply(event("CHANNEL_UPDATE.obfuscated"))

    general = cache.channel(GENERAL)
    assert general is not None
    assert general.obfuscated
    assert general.name == "___hidden___"
    assert general.topic is None
    assert cache.channel(RELEASE_NOTES_THREAD) is None
    assert RELEASE_NOTES_THREAD not in channel_ids(cache)


def test_a_real_update_replaces_an_obfuscated_channel(cache: Cache) -> None:
    hidden = cache.channel(STAFF_ONLY)

    cache.apply(event("CHANNEL_UPDATE"))

    visible = cache.channel(STAFF_ONLY)
    assert hidden is not None
    assert hidden.obfuscated
    assert visible is not None
    assert not visible.obfuscated
    assert visible.name == "staff-only"
    assert visible.topic == "Moderator coordination"


def test_role_create_update_and_delete(cache: Cache) -> None:
    created_id = 1564612126310531073

    cache.apply(event("GUILD_ROLE_CREATE"))
    created = cache.role(created_id)
    cache.apply(event("GUILD_ROLE_UPDATE"))
    updated = cache.role(ADMIN_ROLE)
    cache.apply(event("GUILD_ROLE_DELETE"))

    assert created is not None
    assert created.name == "Tester"
    assert updated is not None
    assert updated.colors.secondary_color == 16759788
    assert cache.role(created_id) is None
    assert created_id not in {role.id for role in cache.guild_roles(GUILD)}
    guild = cache.guild(GUILD)
    assert guild is not None
    assert guild.roles == (*cache.guild_roles(GUILD),)
    assert updated in guild.roles


@pytest.mark.parametrize(
    ("cls", "fixture", "field"),
    [
        (EmojisUpdate, "GUILD_EMOJIS_UPDATE", "emojis"),
        (StickersUpdate, "GUILD_STICKERS_UPDATE", "stickers"),
    ],
)
def test_expression_updates_replace_the_guilds_and_carry_the_old_ones(
    cache: Cache,
    cls: type[EmojisUpdate | StickersUpdate],
    fixture: str,
    field: str,
) -> None:
    before = getattr(cache.guild(GUILD), field)

    received = applied(cache, cls, fixture)

    assert received.old == before
    assert getattr(cache.guild(GUILD), field) == getattr(received, field) != before


def test_user_update_replaces_the_bot_user(cache: Cache) -> None:
    cache.apply(event("USER_UPDATE"))
    bot = cache.user(BOT)

    assert bot is not None
    assert bot.username == "bostie-renamed"
    assert bot.raw_json is None


def test_a_ready_drops_only_the_guilds_of_its_shard(cache: Cache) -> None:
    other = GUILD + (1 << 22)
    shard = (GUILD >> 22) % 2
    cache.apply(event("GUILD_CREATE", id=str(other), channels=[], threads=[]))

    cache.apply(event("READY", shard=[1 - shard, 2]))
    kept = [guild.id for guild in cache.guilds()]
    cache.apply(event("READY", shard=[shard, 2]))

    assert kept == [GUILD]
    assert cache.guilds() == []
    assert cache.channel(GENERAL) is None
    assert cache.role(ADMIN_ROLE) is None
    assert cache.me(GUILD) is None
    assert cache.user(BOT) is not None


@pytest.mark.parametrize(
    ("fixture", "changes", "field", "held"),
    [
        ("GUILD_UPDATE", {}, "old", lambda cache: cache.guild(GUILD)),
        ("GUILD_DELETE.left", {}, "guild", lambda cache: cache.guild(GUILD)),
        ("CHANNEL_UPDATE", {}, "old", lambda cache: cache.channel(STAFF_ONLY)),
        ("GUILD_ROLE_UPDATE", {}, "old", lambda cache: cache.role(ADMIN_ROLE)),
        (
            "GUILD_ROLE_DELETE",
            {"role_id": str(ADMIN_ROLE)},
            "role",
            lambda cache: cache.role(ADMIN_ROLE),
        ),
        ("USER_UPDATE", {}, "old", lambda cache: cache.user(BOT)),
    ],
)
def test_update_and_delete_events_carry_what_the_cache_held(
    cache: Cache,
    fixture: str,
    changes: dict[str, Any],
    field: str,
    held: Callable[[Cache], object],
) -> None:
    before = held(cache)

    received = cache.apply(event(fixture, **changes))

    assert before is not None
    assert getattr(received, field) == before


def test_member_count_follows_joins_and_leaves(cache: Cache) -> None:
    for fixture in ("GUILD_MEMBER_ADD", "GUILD_MEMBER_ADD", "GUILD_MEMBER_REMOVE"):
        cache.apply(event(fixture))

    guild = cache.guild(GUILD)
    assert guild is not None
    assert guild.member_count == 5


def test_voice_states_follow_joins_changes_and_leaves(cache: Cache) -> None:
    owner = cache.voice_state(GUILD, OWNER)

    joined = applied(cache, VoiceStateUpdate, "VOICE_STATE_UPDATE")
    state = cache.voice_state(GUILD, joined.voice_state.user_id)
    changed = applied(cache, VoiceStateUpdate, "VOICE_STATE_UPDATE", self_deaf=False)
    left = applied(cache, VoiceStateUpdate, "VOICE_STATE_UPDATE", channel_id=None)

    assert owner is not None
    assert (owner.guild_id, owner.self_mute) == (GUILD, True)
    assert joined.old is None
    assert state is not None
    assert state.member is None
    assert changed.old == state
    assert left.old is not None
    assert left.old.self_deaf is False
    assert cache.guild_voice_states(GUILD) == [owner]


def test_the_bots_member_follows_only_its_own_updates(cache: Cache) -> None:
    joined = cache.me(GUILD)

    cache.apply(event("GUILD_MEMBER_UPDATE"))
    other = cache.me(GUILD)
    cache.apply(event("GUILD_MEMBER_UPDATE", user=BOT_USER, roles=[], deaf=None))
    renamed = cache.me(GUILD)

    assert joined is not None
    assert (joined.user.id, joined.roles) == (BOT, (BOT_ROLE,))
    assert other == joined
    assert (cache.guild_members(GUILD), cache.user(MODERATOR)) == ([renamed], None)
    assert renamed is not None
    assert (renamed.nick, renamed.roles, renamed.deaf) == (
        "linus (timed out)",
        (),
        False,
    )


def test_the_bots_member_is_found_past_members_whose_fields_hold_its_id() -> None:
    members = event("GUILD_CREATE").raw["members"]
    cache = Cache()
    cache.apply(event("READY"))

    cache.apply(
        event("GUILD_CREATE", members=[members[0] | {"nick": str(BOT)}, *members])
    )
    me = cache.me(GUILD)

    assert me is not None
    assert (me.user.id, cache.guild_members(GUILD)) == (BOT, [me])


def test_the_member_cache_follows_creates_joins_updates_chunks_and_leaves() -> None:
    cache = Cache(members=True)
    cache.apply(event("READY"))
    cache.apply(event("GUILD_CREATE"))
    created = {member.id for member in cache.guild_members(GUILD)}
    moderator = cache.member(GUILD, MODERATOR)

    cache.apply(event("GUILD_MEMBER_ADD"))
    joined = cache.member(GUILD, NEWCOMER)
    updated = applied(cache, MemberUpdate, "GUILD_MEMBER_UPDATE")
    removed = applied(cache, MemberRemove, "GUILD_MEMBER_REMOVE")
    cache.apply(event("GUILD_MEMBERS_CHUNK", members=[updated.raw | {"nick": None}]))
    chunked = cache.member(GUILD, MODERATOR)

    assert created == {OWNER, SUPPORT, MODERATOR, BOT}
    assert joined is not None
    assert joined.raw_json is None
    assert (updated.old, removed.member) == (moderator, joined)
    assert cache.member(GUILD, NEWCOMER) is None
    assert chunked is not None
    assert chunked.nick is None
    assert cache.user(MODERATOR) == chunked.user
    assert cache.me(GUILD) == cache.member(GUILD, BOT)


def test_the_presence_cache_drops_offline_members() -> None:
    cache = Cache(presences=True)
    cache.apply(event("READY"))
    cache.apply(event("GUILD_CREATE"))
    created = {presence.user.id for presence in cache.guild_presences(GUILD)}
    support = cache.presence(GUILD, SUPPORT)

    updated = applied(cache, PresenceUpdate, "PRESENCE_UPDATE")
    offline = applied(cache, PresenceUpdate, "PRESENCE_UPDATE", status="offline")
    cache.apply(event("GUILD_MEMBERS_CHUNK"))
    moderator = cache.presence(GUILD, MODERATOR)

    assert created == {OWNER, SUPPORT, BOT}
    assert support is not None
    assert updated.old == support
    assert offline.old is not None
    assert offline.old.status is Status.DND
    assert cache.presence(GUILD, SUPPORT) is None
    assert moderator is not None
    assert moderator.status is Status.IDLE
    assert cache.member(GUILD, MODERATOR) is None
    assert {"PRESENCE_UPDATE", "GUILD_MEMBERS_CHUNK"} <= cache.names
    assert {"PRESENCE_UPDATE", "GUILD_MEMBERS_CHUNK"}.isdisjoint(Cache().names)


def test_permissions_come_from_the_cached_guild_roles_and_overwrites(
    cache: Cache,
) -> None:
    everyone = cache.role(GUILD)
    bot_role = cache.role(BOT_ROLE)
    general = cache.permissions(GENERAL)

    assert everyone is not None
    assert bot_role is not None
    assert cache.guild_permissions(GUILD) == everyone.permissions | bot_role.permissions
    assert general == everyone.permissions | bot_role.permissions
    assert cache.permissions(RELEASE_NOTES_THREAD) == general
    assert cache.permissions(STAFF_ONLY) == Permissions(0)
    assert cache.permissions(1) is None


@pytest.mark.parametrize(
    ("permissions", "kept"), [(Permissions.MANAGE_THREADS, True), (0, False)]
)
def test_leaving_a_private_thread_drops_it_unless_the_bot_manages_threads(
    cache: Cache, permissions: int, kept: bool
) -> None:
    role = event("GUILD_CREATE").raw["roles"][1] | {"permissions": str(permissions)}
    cache.apply(event("GUILD_ROLE_UPDATE", role=role))
    cache.apply(event("THREAD_CREATE", type=12))

    cache.apply(
        event(
            "THREAD_MEMBERS_UPDATE",
            id=str(FORUM_THREAD),
            removed_member_ids=[BOT_USER["id"]],
        )
    )

    assert (cache.channel(FORUM_THREAD) is not None) is kept


def test_the_message_cache_keeps_the_latest_and_hands_them_to_events() -> None:
    cache = Cache(max_messages=2)
    ids = [str(GUILD + number) for number in range(3)]
    for id in ids:
        cache.apply(event("MESSAGE_CREATE", id=id))
    second, third = cache.message(int(ids[1])), cache.message(int(ids[2]))

    edited = applied(cache, MessageUpdate, "MESSAGE_UPDATE", id=ids[1])
    deleted = applied(cache, MessageDelete, "MESSAGE_DELETE", id=ids[2])
    bulk = applied(cache, MessageDeleteBulk, "MESSAGE_DELETE_BULK", ids=ids)

    assert cache.message(int(ids[0])) is None
    assert edited.old == second
    assert deleted.message == third
    assert [message.content for message in bulk.messages] == ["hello bostie (edited)"]
    assert cache.message(int(ids[1])) is None
    assert "MESSAGE_CREATE" in cache.names
    assert "MESSAGE_CREATE" not in Cache().names


def test_dm_messages_keep_the_latest_dm_channels_without_the_messages() -> None:
    cache = Cache(max_dm_channels=2)
    cache.apply(event("READY"))
    for number in (1, 2, 3):
        author = BOT_USER | {"id": str(number)}
        cache.apply(
            event(
                "MESSAGE_CREATE",
                guild_id=None,
                author=author,
                channel_id=str(GUILD + number),
            )
        )
    cache.apply(event("MESSAGE_CREATE", guild_id=None, author=BOT_USER, channel_id="4"))
    cache.apply(event("MESSAGE_CREATE", author=BOT_USER | {"id": "5"}))
    third = cache.dm_channel(3)

    assert (cache.dm_channel(1), cache.channel(GUILD + 1)) == (None, None)
    assert third is not None
    assert (third.id, third.type) == (GUILD + 3, ChannelType.DM)
    assert cache.channel(GUILD + 3) == third
    assert cache.user(3) == third.recipients[0]
    assert [cache.dm_channel(id) for id in (BOT, 5)] == [None, None]
    assert cache.message(int(event("MESSAGE_CREATE").raw["id"])) is None


def test_events_the_cache_does_not_consume_change_nothing(cache: Cache) -> None:
    before = (cache.guilds(), cache.guild_channels(GUILD), cache.guild_roles(GUILD))

    cache.apply(event("MESSAGE_CREATE"))
    cache.apply(event("INTERACTION_CREATE.command"))

    assert (cache.guilds(), cache.guild_channels(GUILD), cache.guild_roles(GUILD)) == (
        before
    )
    assert "INTERACTION_CREATE" not in cache.names


def test_cached_channels_never_hold_fields_channel_update_leaves_stale(
    cache: Cache,
) -> None:
    cache.apply(event("THREAD_UPDATE"))
    cache.apply(event("CHANNEL_UPDATE"))
    received = event("GUILD_CREATE").raw

    assert received["channels"][1]["last_message_id"] is not None
    assert received["threads"][0]["message_count"] is not None
    assert all(
        (
            channel.last_message_id,
            channel.message_count,
            channel.member_count,
            channel.last_pin_timestamp,
        )
        == (None, None, None, None)
        for channel in cache.guild_channels(GUILD)
    )


def test_an_obfuscated_update_without_guild_id_is_filed_by_channel_id(
    cache: Cache,
) -> None:
    cache.apply(event("CHANNEL_UPDATE.obfuscated", guild_id=None))

    general = cache.channel(GENERAL)
    assert general is not None
    assert general.obfuscated
    assert general.guild_id == GUILD
    assert general in cache.guild_channels(GUILD)
    assert cache.channel(RELEASE_NOTES_THREAD) is None
