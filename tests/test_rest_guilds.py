from bostie import Bot
from bostie.testing import FakeDiscord, payloads, run, running

PNG = b"\x89PNG\r\n\x1a\n"


def test_the_widget_image_comes_back_as_the_png_bytes_discord_sent() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            fake.answer("GET", "/guilds/1/widget.png", PNG, "image/png")
            image = await bot.rest.guilds.fetch_widget_image(1, style="banner2")
            assert image == PNG
            assert fake.requests[-1].query == {"style": "banner2"}

    run(scenario)


def test_prune_sends_roles_as_csv_to_count_and_as_an_array_to_begin() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            guilds = bot.rest.guilds
            fake.fail("GET", "/guilds/1/prune", 200, {"pruned": 3})
            assert await guilds.fetch_prune_count(1, days=7, include_roles=[5, 6]) == 3
            assert fake.requests[-1].query == {"days": "7", "include_roles": "5,6"}

            fake.fail("POST", "/guilds/1/prune", 200, {"pruned": None})
            pruned = await guilds.begin_prune(
                1, compute_prune_count=False, include_roles=[5, 6], reason="tidy"
            )
            assert pruned is None
            assert fake.requests[-1].json == {
                "compute_prune_count": False,
                "include_roles": [5, 6],
            }

    run(scenario)


def test_the_guild_list_walks_forward_from_the_oldest_guild() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        guild = payloads.fixture("rest/current_user_guild")
        async with running(bot, fake):
            for ids in (range(1, 201), range(201, 251)):
                page = [guild | {"id": str(id)} for id in ids]
                fake.fail("GET", "/users/@me/guilds", 200, page)
            listed = [guild.id async for guild in bot.rest.guilds.list()]
            assert listed == list(range(1, 251))
            assert [request.query for request in fake.requests[-2:]] == [
                {"limit": "200"},
                {"limit": "200", "after": "200"},
            ]

    run(scenario)
