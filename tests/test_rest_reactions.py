from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from bostie import Bot, ReactionType, User
from bostie.rest import REST
from bostie.testing import FakeDiscord, payloads, run, running


@pytest.mark.parametrize(
    ("walk", "route", "page", "query"),
    [
        (
            lambda rest: rest.reactions.list(1, 2, "lyre:3", type=ReactionType.BURST),
            "/channels/1/messages/2/reactions/lyre:3",
            lambda users: users,
            {"type": "1"},
        ),
        (
            lambda rest: rest.polls.list_answer_voters(1, 2, 3),
            "/channels/1/polls/2/answers/3",
            lambda users: {"users": users},
            {},
        ),
    ],
)
def test_reactors_and_voters_are_walked_after_the_largest_user_id(
    walk: Callable[[REST], AsyncIterator[User]],
    route: str,
    page: Callable[[list[dict[str, Any]]], object],
    query: dict[str, str],
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        pages = [range(100, 0, -1), range(130, 100, -1)]
        async with running(bot, fake):
            for ids in pages:
                users = [payloads.user(id=str(id)) for id in ids]
                fake.fail("GET", route, 200, page(users))
            walked = [user async for user in walk(bot.rest)]
            assert [(user.id, user.raw["id"]) for user in walked] == [
                (id, str(id)) for ids in pages for id in ids
            ]
            assert [request.query for request in fake.requests[-2:]] == [
                query | {"limit": "100"},
                query | {"limit": "100", "after": "100"},
            ]

    run(scenario)
