from bostie import Bot, Permissions
from bostie.testing import FakeDiscord, payloads, run, running


def test_role_positions_go_out_as_an_array_and_every_role_comes_back() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            roles = [payloads.role(id=str(id)) for id in (1, 5, 6)]
            fake.fail("PATCH", "/guilds/1/roles", 200, roles)
            moved = await bot.rest.roles.edit_positions(1, {5: 2, 6: None})
            assert fake.requests[-1].json == [
                {"id": 5, "position": 2},
                {"id": 6, "position": None},
            ]
            assert [role.id for role in moved] == [1, 5, 6]

    run(scenario)


def test_member_counts_are_keyed_by_role_id_and_permissions_typed() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            fake.fail("GET", "/guilds/1/roles/member-counts", 200, {"5": 1337})
            assert await bot.rest.roles.fetch_member_counts(1) == {5: 1337}
            fake.fail("GET", "/guilds/1/permissions/role/5", 200, {"permissions": "40"})
            permissions = await bot.rest.roles.fetch_permissions(1, 5)
            assert permissions == Permissions.MANAGE_GUILD | Permissions.ADMINISTRATOR

    run(scenario)
