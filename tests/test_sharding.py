import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterable
from typing import Literal

import pytest
from fakediscord import Connection, FakeDiscord, payloads, run

from bostie import Bot, Status, activity
from bostie.events import GuildCreate, Ready

GUILD_ON_SHARD_1 = 1 << 22


def recommend(fake: FakeDiscord, shards: int, max_concurrency: int) -> None:
    info = payloads.fixture("rest/gateway_bot")
    limit = info["session_start_limit"] | {"max_concurrency": max_concurrency}
    fake.fail(
        "GET",
        "/gateway/bot",
        200,
        info
        | {"url": fake.gateway_url, "shards": shards, "session_start_limit": limit},
    )


def ops(connection: Connection) -> list[int]:
    return [payload["op"] for payload in connection.received]


async def received(fake: FakeDiscord, *wanted: int) -> None:
    pending = list(wanted)
    while pending:
        op = (await fake.gateway_received.get())["op"]
        if op in pending:
            pending.remove(op)


@contextlib.asynccontextmanager
async def starting(bot: Bot, fake: FakeDiscord) -> AsyncIterator[None]:
    started = asyncio.create_task(bot.start(fake.token))
    try:
        yield
    finally:
        await bot.close()
        await started


def test_a_bot_runs_every_recommended_shard_and_routes_by_guild() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        recommend(fake, shards=2, max_concurrency=2)
        release = fake.hold_identify(1)
        bot = Bot(api_url=fake.api_url)
        first = asyncio.ensure_future(bot.wait_for(Ready))
        async with starting(bot, fake):
            ready = asyncio.create_task(bot.wait_until_ready())
            assert (await fake.next_identify())["shard"] == [0, 2]
            assert (await first).shard_id == 0
            assert not ready.done()
            release.set()
            assert (await fake.next_identify())["shard"] == [1, 2]
            await ready

            guild = payloads.guild(id=str(GUILD_ON_SHARD_1))
            created, _ = await asyncio.gather(
                bot.wait_for(GuildCreate), fake.dispatch("GUILD_CREATE", guild, shard=1)
            )
            assert created.shard_id == 1
            assert bot.gateway.shard_for(GUILD_ON_SHARD_1) == 1

            bot.gateway.send(8, {"guild_id": str(GUILD_ON_SHARD_1), "limit": 0})
            bot.gateway.send(3, {"since": None, "activities": [], "status": "idle"})
            await received(fake, 8, 3, 3)
            shards = {connection.shard: connection for connection in fake.connections}
            assert [op for op in ops(shards[0]) if op != 1] == [2, 3]
            assert [op for op in ops(shards[1]) if op != 1] == [2, 8, 3]

    run(scenario)


def test_update_presence_reaches_every_shard_and_every_later_identify() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        recommend(fake, shards=2, max_concurrency=2)
        release = fake.hold_hello()
        bot = Bot(api_url=fake.api_url)
        first = asyncio.ensure_future(bot.wait_for(Ready))
        async with starting(bot, fake):
            await first
            await bot.update_presence(
                status=Status.DND, activities=[activity.custom("rolling dice")]
            )
            release.set()
            identifies = [await fake.next_identify() for _ in range(2)]
            await received(fake, 3, 3)

        presence = {
            "since": None,
            "activities": [
                {"name": "Custom Status", "type": 4, "state": "rolling dice"}
            ],
            "status": "dnd",
            "afk": False,
        }
        assert identifies[1]["presence"] == presence
        for connection in fake.connections:
            assert [p["d"] for p in connection.received if p["op"] == 3] == [presence]

    run(scenario)


@pytest.mark.parametrize(
    ("shard_count", "shard_ids", "error"),
    [
        (0, "all", "at least 1"),
        ("auto", [0], "needs an explicit shard_count"),
        (4, [], "distinct and not empty"),
        (4, [1, 1], "distinct and not empty"),
        (4, [3, 4], r"\[4\] are outside range\(4\)"),
    ],
)
def test_shard_settings_are_checked_when_the_bot_is_built(
    shard_count: int | Literal["auto"],
    shard_ids: Iterable[int] | Literal["all"],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        Bot(shard_count=shard_count, shard_ids=shard_ids)


def test_a_cluster_process_identifies_only_its_shards() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        recommend(fake, shards=1, max_concurrency=4)
        bot = Bot(api_url=fake.api_url, shard_count=4, shard_ids=[1, 3])
        async with starting(bot, fake):
            identified = sorted(
                [(await fake.next_identify())["shard"] for _ in range(2)]
            )
            assert identified == [[1, 4], [3, 4]]
            await bot.wait_until_ready()
            assert bot.gateway.shard_ids == (1, 3)
            with pytest.raises(ValueError, match="shard 0 does not run here"):
                bot.gateway.send(43, {"channel_id": "1"})

    run(scenario)
