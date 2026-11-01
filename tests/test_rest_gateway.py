from fakediscord import FakeDiscord, run, running

from bostie import Bot


def test_the_gateway_url_and_the_bots_session_start_limit_decode() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            gateway = bot.rest.gateway
            fake.fail("GET", "/gateway", 200, {"url": "wss://gateway.discord.gg/"})
            assert (await gateway.fetch()).url == "wss://gateway.discord.gg/"
            info = await gateway.fetch_bot()
            assert (info.url, info.shards) == (fake.gateway_url, 1)
            assert info.session_start_limit.max_concurrency == 1

    run(scenario)
