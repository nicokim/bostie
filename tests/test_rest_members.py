from fakediscord import FakeDiscord, payloads, run, running

from bostie import Bot


def test_adding_a_member_returns_none_when_they_already_were_one() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            members = bot.rest.members
            fake.fail("PUT", "/guilds/1/members/2", 201, payloads.member())
            added = await members.add(1, 2, access_token="oauth", nick="new")
            assert fake.requests[-1].json == {"access_token": "oauth", "nick": "new"}
            assert added is not None
            assert added.id == int(payloads.member()["user"]["id"])

            fake.fail("PUT", "/guilds/1/members/2", 204, None)
            assert await members.add(1, 2, access_token="oauth") is None

    run(scenario)
