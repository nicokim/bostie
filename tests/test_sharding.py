import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Iterable
from typing import Literal

import pytest

from bostie import Bot, Status, activity
from bostie.events import GuildCreate, Ready
from bostie.testing import Connection, FakeDiscord, payloads, run, running

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
            bot.gateway.send(31, {"guild_ids": ["1", str(GUILD_ON_SHARD_1), "2"]})
            await received(fake, 8, 3, 3, 31, 31)
            shards = {connection.shard: connection for connection in fake.connections}
            assert [op for op in ops(shards[0]) if op != 1] == [2, 3, 31]
            assert [op for op in ops(shards[1]) if op != 1] == [2, 8, 3, 31]
            assert [
                payload["d"]["guild_ids"]
                for shard in (0, 1)
                for payload in shards[shard].received
                if payload["op"] == 31
            ] == [["1", "2"], [str(GUILD_ON_SHARD_1)]]

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
            with pytest.raises(ValueError, match="shard 0 does not run here"):
                await bot.request_members(4 << 22, query="a")

    run(scenario)


def test_shards_identify_only_after_before_identify_returns() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        recommend(fake, shards=2, max_concurrency=2)
        asked: asyncio.Queue[int] = asyncio.Queue()
        release = asyncio.Event()

        async def before_identify(shard_id: int) -> None:
            asked.put_nowait(shard_id)
            await release.wait()

        bot = Bot(api_url=fake.api_url, before_identify=before_identify)
        async with starting(bot, fake):
            first = sorted([await asked.get(), await asked.get()])
            await received(fake, 1, 1)
            waited = [2 in ops(connection) for connection in fake.connections]
            release.set()
            identified = sorted(
                [(await fake.next_identify())["shard"] for _ in range(2)]
            )

        assert (first, waited) == ([0, 1], [False, False])
        assert identified == [[0, 2], [1, 2]]

    run(scenario, heartbeat_interval=50)


def test_a_before_identify_that_raises_is_logged_and_the_shard_identifies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def before_identify(shard_id: int) -> None:
        raise LookupError(shard_id)

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, before_identify=before_identify)
        async with running(bot, fake):
            assert bot.is_ready

    with caplog.at_level(logging.ERROR, "bostie.bot"):
        run(scenario)
    assert caplog.messages == [
        "before_identify raised for shard 0; it identifies anyway"
    ]
