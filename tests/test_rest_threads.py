from datetime import UTC, datetime, timedelta
from typing import Any

from fakediscord import FakeDiscord, payloads, run, running

from bostie import Bot, File

MENTIONS = {"parse": ["users", "roles"]}


def archived(index: int, newest: datetime) -> dict[str, Any]:
    thread: dict[str, Any] = payloads.fixture("rest/active_threads")["threads"][0]
    when = (newest - timedelta(minutes=index)).isoformat()
    return thread | {
        "id": str(5000 - index),
        "thread_metadata": thread["thread_metadata"] | {"archive_timestamp": when},
    }


def test_archived_threads_walk_back_in_time_and_never_ask_for_one() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        newest = datetime(2026, 10, 27, 12, tzinfo=UTC)
        threads = [archived(index, newest) for index in range(102)]
        async with running(bot, fake):
            route = "/channels/1/threads/archived/public"
            for page in (threads[:100], threads[100:]):
                answer = {"threads": page, "members": [], "has_more": True}
                fake.fail("GET", route, 200, answer)
            walked = bot.rest.threads.list_public_archived(1, limit=101)
            assert [thread.id async for thread in walked] == list(range(5000, 4899, -1))
            last = threads[99]["thread_metadata"]["archive_timestamp"]
            assert [request.query for request in fake.requests[-2:]] == [
                {"limit": "100"},
                {"limit": "2", "before": last},
            ]

    run(scenario)


def test_thread_members_are_walked_with_their_guild_members() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        member = payloads.fixture("rest/active_threads")["members"][0]
        async with running(bot, fake):
            page = [
                member | {"user_id": str(id), "member": payloads.member()}
                for id in (8, 9)
            ]
            fake.fail("GET", "/channels/1/thread-members", 200, page)
            walked = [
                member async for member in bot.rest.threads.list_members(1, after=7)
            ]
            assert [
                (member.user_id, member.member is not None) for member in walked
            ] == [
                (8, True),
                (9, True),
            ]
            assert fake.requests[-1].query == {
                "after": "7",
                "with_member": "true",
                "limit": "100",
            }

    run(scenario)


def test_a_forum_thread_sends_its_first_message_with_files() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        thread = payloads.fixture("rest/active_threads")["threads"][0]
        async with running(bot, fake):
            fake.fail("POST", "/channels/1/threads", 200, thread)
            started = await bot.rest.threads.start_in_forum(
                1,
                name="Crash on join",
                applied_tags=[3],
                message={"content": "Steps below", "files": [File("log.txt", b"x")]},
            )
            request = fake.requests[-1]
            assert request.json == {
                "name": "Crash on join",
                "applied_tags": [3],
                "message": {
                    "content": "Steps below",
                    "allowed_mentions": MENTIONS,
                    "attachments": [{"id": 0, "filename": "log.txt"}],
                },
            }
            assert [(part.name, part.data) for part in request.parts[1:]] == [
                ("files[0]", b"x")
            ]
            assert started.id == int(thread["id"])

    run(scenario)
