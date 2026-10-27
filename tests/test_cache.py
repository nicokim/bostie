from pathlib import Path
from typing import Any

import msgspec
import pytest

from bostie.cache import Cache
from bostie.events import EVENTS, Event

EVENT_FIXTURES = Path(__file__).parent / "fixtures" / "models" / "events"
GUILD = 1335172792320131073
GENERAL = 1335173295636611073
STAFF_ONLY = 1335175308902531073
HELP_FORUM = 1382046656102531073
RELEASE_NOTES_THREAD = 1564337818828931073
FORUM_THREAD = 1564617159475331073
MOD_REVIEW_THREAD = 1564005629952131073
ADMIN_ROLE = 1335174050611331073


def event(fixture: str, **changes: Any) -> Event:
    name = fixture.split(".")[0]
    document = msgspec.json.decode((EVENT_FIXTURES / f"{fixture}.json").read_bytes())
    document.update(changes)
    return EVENTS[name].decode(0, msgspec.json.encode(document))


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

    assert cache.user is not None
    assert cache.user.username == "bostie-test"
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


def test_guild_update_replaces_the_header_and_keeps_the_member_count(
    cache: Cache,
) -> None:
    cache.apply(event("GUILD_UPDATE"))

    guild = cache.guild(GUILD)
    assert guild is not None
    assert guild.description == "Fixture guild for bostie"
    assert guild.verification_level == 2
    assert guild.member_count == 4


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


def test_thread_create_update_and_delete(cache: Cache) -> None:
    cache.apply(event("THREAD_CREATE"))
    created = cache.channel(FORUM_THREAD)
    cache.apply(event("THREAD_UPDATE"))
    archived = cache.channel(RELEASE_NOTES_THREAD)
    cache.apply(event("THREAD_DELETE"))

    assert created is not None
    assert created.parent_id == HELP_FORUM
    assert archived is not None
    assert archived.thread_metadata is not None
    assert archived.thread_metadata.archived is True
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
    assert updated.colors is not None
    assert updated.colors.secondary_color == 16759788
    assert cache.role(created_id) is None
    assert created_id not in {role.id for role in cache.guild_roles(GUILD)}


def test_ready_of_a_new_session_clears_everything(cache: Cache) -> None:
    cache.apply(event("READY"))

    assert cache.guilds() == []
    assert cache.channel(GENERAL) is None
    assert cache.role(ADMIN_ROLE) is None
    assert cache.user is not None


def test_events_the_cache_does_not_consume_change_nothing(cache: Cache) -> None:
    before = (cache.guilds(), cache.guild_channels(GUILD), cache.guild_roles(GUILD))

    cache.apply(event("MESSAGE_CREATE"))
    cache.apply(event("INTERACTION_CREATE.command"))

    assert (cache.guilds(), cache.guild_channels(GUILD), cache.guild_roles(GUILD)) == (
        before
    )
    assert "INTERACTION_CREATE" not in Cache.NAMES


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
