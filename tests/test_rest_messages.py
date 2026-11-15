import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bostie import Bot, MessageReference, MessageReferenceType
from bostie.events import GuildCreate
from bostie.models import PollAnswer, PollCreateRequest, PollMedia
from bostie.testing import FakeDiscord, payloads, run, running

MENTIONS = {"parse": ["users", "roles"]}


async def started(bot: Bot, fake: FakeDiscord) -> None:
    await asyncio.gather(
        bot.wait_for(GuildCreate), fake.dispatch("GUILD_CREATE", payloads.guild())
    )


def test_a_message_forwards_with_its_extras_or_replies_but_not_both() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        channel_id = int(payloads.channel()["id"])
        forward = MessageReference(
            type=MessageReferenceType.FORWARD, message_id=5, channel_id=6
        )
        poll = PollCreateRequest(
            question=PollMedia(text="Codec?"),
            answers=(PollAnswer(poll_media=PollMedia(text="Opus")),),
            duration=2,
        )
        async with running(bot, fake):
            await bot.rest.messages.create(
                channel_id,
                message_reference=forward,
                nonce="n-1",
                enforce_nonce=True,
                sticker_ids=[7],
                poll=poll,
            )
            assert fake.requests[-1].json == {
                "message_reference": {"type": 1, "message_id": 5, "channel_id": 6},
                "nonce": "n-1",
                "enforce_nonce": True,
                "sticker_ids": [7],
                "poll": {
                    "question": {"text": "Codec?"},
                    "answers": [{"poll_media": {"text": "Opus"}}],
                    "duration": 2,
                },
                "allowed_mentions": MENTIONS,
            }
            sent = len(fake.requests)
            with pytest.raises(ValueError, match="mutually exclusive"):
                await bot.rest.messages.create(
                    channel_id, "hi", reply_to=5, message_reference=forward
                )
            assert len(fake.requests) == sent

    run(scenario)


def test_pins_are_walked_before_the_oldest_pin_time_of_each_page() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        guild_id = int(payloads.guild()["id"])
        channel_id = int(payloads.channel()["id"])
        newest = datetime(2026, 10, 27, 12, tzinfo=UTC)
        message = payloads.fixture("rest/message") | {"channel_id": str(channel_id)}
        pins = [
            {
                "pinned_at": (newest - timedelta(minutes=index)).isoformat(),
                "message": message | {"id": str(1000 - index)},
            }
            for index in range(60)
        ]
        async with running(bot, fake):
            await started(bot, fake)
            route = f"/channels/{channel_id}/messages/pins"
            fake.fail("GET", route, 200, {"items": pins[:50], "has_more": True})
            fake.fail("GET", route, 200, {"items": pins[50:], "has_more": False})
            listed = [
                pin async for pin in bot.rest.messages.list_pins(channel_id, limit=60)
            ]
            assert [pin.message.id for pin in listed] == list(range(1000, 940, -1))
            assert listed[-1].pinned_at == newest - timedelta(minutes=59)
            assert listed[0].raw == pins[0]
            assert listed[0].message.guild_id == guild_id
            assert [request.query for request in fake.requests[-2:]] == [
                {"limit": "50"},
                {"limit": "10", "before": pins[49]["pinned_at"]},
            ]

    run(scenario)


def test_a_search_waits_while_discord_indexes_and_flattens_its_matches() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        guild_id = int(payloads.guild()["id"])
        message = payloads.fixture("rest/message")
        indexing = {
            "message": "Index not yet available. Try again later",
            "code": 110000,
            "documents_indexed": 0,
            "retry_after": 0.001,
        }
        found = {
            "doing_deep_historical_index": False,
            "total_results": 2,
            "messages": [[message | {"id": "11"}], [message | {"id": "12"}]],
            "threads": [payloads.channel()],
            "members": [],
        }
        async with running(bot, fake):
            route = f"/guilds/{guild_id}/messages/search"
            fake.fail("GET", route, 202, indexing)
            fake.fail("GET", route, 200, found)
            results = await bot.rest.messages.search(
                guild_id, content="bostie", pinned=False
            )
            assert [request.query for request in fake.requests[-2:]] == 2 * [
                {"content": "bostie", "pinned": "false"}
            ]
            assert [match.id for match in results.messages] == [11, 12]
            assert {match.guild_id for match in results.messages} == {guild_id}
            assert (results.total_results, len(results.threads)) == (2, 1)
            assert results.raw == found

    run(scenario)


def test_a_purge_bulk_deletes_young_messages_and_deletes_old_ones_alone() -> None:
    channel = "7"
    mine, other = "11", "12"
    now = datetime.now(UTC).timestamp()

    def seeded(age: float, author: str, kind: int = 0) -> dict[str, Any]:
        return payloads.message(
            id=payloads.snowflake(now - age),
            channel_id=channel,
            author=payloads.user(id=author),
            type=kind,
        )

    young = [seeded(60 + second, mine) for second in range(101)]
    old = seeded(20 * 86400, mine)
    kept = [seeded(30, other), seeded(40, mine, kind=1)]

    async def scenario(fake: FakeDiscord) -> None:
        fake.messages[channel] = {
            message["id"]: message for message in [*young, old, *kept]
        }
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            deleted = await bot.rest.messages.purge(
                int(channel),
                limit=None,
                check=lambda message: message.author.id == int(mine),
                reason="cleanup",
            )

        changes = [request for request in fake.requests if request.method != "GET"]
        assert [str(message.id) for message in deleted] == [
            message["id"] for message in [*young, old]
        ]
        assert [(request.method, request.path) for request in changes] == [
            ("POST", f"/channels/{channel}/messages/bulk-delete"),
            ("DELETE", f"/channels/{channel}/messages/{old['id']}"),
            ("DELETE", f"/channels/{channel}/messages/{young[-1]['id']}"),
        ]
        assert len(changes[0].json["messages"]) == 100
        assert {request.headers["x-audit-log-reason"] for request in changes} == {
            "cleanup"
        }
        assert list(fake.messages[channel]) == [message["id"] for message in kept]

    run(scenario)


def test_a_purge_without_bulk_deletes_each_message_alone() -> None:
    channel = "7"
    messages = [payloads.message(channel_id=channel) for _ in range(3)]

    async def scenario(fake: FakeDiscord) -> None:
        fake.messages[channel] = {message["id"]: message for message in messages}
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            deleted = await bot.rest.messages.purge(int(channel), bulk=False)

        changes = [request for request in fake.requests if request.method != "GET"]
        assert len(deleted) == 3
        assert [request.method for request in changes] == ["DELETE"] * 3
        assert fake.messages[channel] == {}

    run(scenario)
