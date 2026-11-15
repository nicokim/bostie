import asyncio

import pytest

from bostie import Bot, Intents
from bostie.events import GuildCreate, MessageCreate
from bostie.testing import FakeDiscord, payloads, run, running

GUILD = int(payloads.guild()["id"])
CHANNEL = int(payloads.channel()["id"])
MEMBER = int(payloads.member()["user"]["id"])
ROLE = 1335174050611331073
MESSAGE = payloads.message()


@pytest.mark.parametrize("cached", [True, False])
def test_lookups_read_the_cache_and_ask_rest_for_what_it_lacks(cached: bool) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = (
            Bot(
                api_url=fake.api_url,
                intents=Intents.DEFAULT | Intents.GUILD_MEMBERS,
                members=True,
                chunk_members=False,
                max_messages=10,
            )
            if cached
            else Bot(api_url=fake.api_url, cache=False)
        )
        message_id = int(MESSAGE["id"])
        fake.messages[str(CHANNEL)] = {MESSAGE["id"]: MESSAGE}
        fake.fail(
            "GET", f"/guilds/{GUILD}/roles/{ROLE}", 200, payloads.role(id=str(ROLE))
        )
        async with running(bot, fake):
            if cached:
                await asyncio.gather(
                    bot.wait_for(GuildCreate),
                    fake.dispatch("GUILD_CREATE", payloads.guild()),
                )
                await asyncio.gather(
                    bot.wait_for(MessageCreate),
                    fake.dispatch("MESSAGE_CREATE", MESSAGE),
                )
            before = len(fake.requests)
            found = [
                (await bot.lookup.user(MEMBER)).id,
                (await bot.lookup.member(GUILD, MEMBER)).user.id,
                (await bot.lookup.channel(CHANNEL)).id,
                (await bot.lookup.guild(GUILD)).id,
                (await bot.lookup.role(GUILD, ROLE)).id,
                (await bot.lookup.message(CHANNEL, message_id)).id,
            ]
            asked = [request.path for request in fake.requests[before:]]

        assert found == [MEMBER, MEMBER, CHANNEL, GUILD, ROLE, message_id]
        assert asked == (
            []
            if cached
            else [
                f"/users/{MEMBER}",
                f"/guilds/{GUILD}/members/{MEMBER}",
                f"/channels/{CHANNEL}",
                f"/guilds/{GUILD}",
                f"/guilds/{GUILD}/roles/{ROLE}",
                f"/channels/{CHANNEL}/messages/{message_id}",
            ]
        )

    run(scenario)
