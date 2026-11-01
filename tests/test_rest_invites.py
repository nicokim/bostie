from typing import Any

import pytest
from fakediscord import FakeDiscord, Part, payloads, run, running

from bostie import Bot, File, Invite, InviteWithMetadata, UnexpectedResponse

METADATA = {
    "uses": 0,
    "max_uses": 1,
    "max_age": 0,
    "temporary": False,
    "created_at": "2016-03-31T19:15:39.954000+00:00",
}


def test_target_users_go_up_as_a_csv_file_part_beside_the_json() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            invites = bot.rest.invites
            csv = File("users.csv", b"user_id\n5\n")
            invite = payloads.fixture("rest/invite") | METADATA
            fake.fail("POST", "/channels/1/invites", 200, invite)
            created = await invites.create(
                1, max_uses=1, target_users_file=csv, reason="event"
            )
            sent = fake.requests[-1]
            assert (sent.json, sent.parts[1:]) == (
                {"max_uses": 1},
                [Part("target_users_file", "users.csv", "text/csv", csv.data)],
            )
            assert sent.headers["x-audit-log-reason"] == "event"
            assert isinstance(created, InviteWithMetadata)

            fake.fail("PUT", f"/invites/{created.code}/target-users", 204, None)
            await invites.update_target_users(created.code, target_users_file=csv)
            assert fake.requests[-1].parts == [
                Part("target_users_file", "users.csv", "text/csv", csv.data)
            ]

    run(scenario)


def test_guild_invites_carry_their_metadata_only_when_discord_sends_it() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            invite: dict[str, Any] = payloads.fixture("rest/invite")
            fake.fail("GET", "/guilds/1/invites", 200, [invite, invite | METADATA])
            bare, full = await bot.rest.invites.list_guild(1)
            assert type(bare) is Invite
            assert bare.raw == invite
            assert isinstance(full, InviteWithMetadata)
            assert full.max_uses == 1

    run(scenario)


def test_target_users_are_read_as_ids_from_their_csv_answer() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            invites = bot.rest.invites
            route = "/invites/abc/target-users"
            fake.answer("GET", route, b"user_id\r\n5\r\n6\r\n", "text/csv")
            assert await invites.list_target_users("abc") == (5, 6)
            fake.answer("GET", route, b"id\n5\n", "text/csv")
            with pytest.raises(UnexpectedResponse, match="under a user_id header"):
                await invites.list_target_users("abc")

    run(scenario)
