import ast
import asyncio
import inspect
import re
import textwrap
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from fakediscord import FakeDiscord, payloads, run, running

from bostie import Bot, File, NotFound, UnexpectedResponse
from bostie.cache import Cache
from bostie.events import GuildCreate
from bostie.models import AllowedMentions
from bostie.rest import REST

if TYPE_CHECKING:
    from bostie.http import HTTPClient

FIXTURES = Path(__file__).parent / "fixtures" / "rest"
MENTIONS = {"parse": ["users", "roles"]}
VERBS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

type Shape = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]


def rows(path: Path) -> list[list[str]]:
    lines = path.read_text().splitlines()
    return [line.split() for line in lines if not line.startswith("#")]


def pending_files() -> list[Path]:
    return sorted((FIXTURES / "pending").glob("*.txt"))


def operation(verb: str, path: str) -> tuple[str, str]:
    return verb, re.sub(r"\{\w+\}", "{}", path)


def implemented(rest: REST) -> dict[str, Shape]:
    found: dict[str, Shape] = {}
    for group_name, group in vars(rest).items():
        for name, function in vars(type(group)).items():
            if name.startswith("_") or not inspect.isfunction(function):
                continue
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
            texts = [
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            ]
            parameters = inspect.signature(function).parameters.values()
            found[f"{group_name}.{name}"] = (
                tuple(text for text in texts if text in VERBS) or ("GET",),
                tuple(text for text in texts if text.startswith("/")),
                tuple(
                    parameter.name
                    for parameter in parameters
                    if parameter.kind is parameter.POSITIONAL_ONLY
                    and parameter.name != "self"
                ),
                any(parameter.name == "reason" for parameter in parameters),
            )
    return found


def test_the_rest_groups_implement_every_table_row_not_pending() -> None:
    table = rows(FIXTURES / "operations.txt")
    pending = {row[2] for path in pending_files() for row in rows(path)}
    rest = REST(cast("HTTPClient", None), AllowedMentions(), Cache())

    groups = {name.split(".")[0] for _, _, name, *_ in table}
    assert {name: type(group).__module__ for name, group in vars(rest).items()} == {
        group: f"bostie.rest.{group}" for group in groups
    }
    assert implemented(rest) == {
        name: (
            (verb,),
            (path,),
            tuple(re.findall(r"\{(\w+)\}", path)),
            "reason" in marks,
        )
        for verb, path, name, *marks in table
        if name not in pending
    }


def test_pending_rows_are_table_rows_of_their_own_group() -> None:
    table = {tuple(row) for row in rows(FIXTURES / "operations.txt")}
    for path in pending_files():
        listed = [tuple(row) for row in rows(path)]
        assert listed
        assert len(set(listed)) == len(listed)
        assert set(listed) <= table
        assert {row[2].split(".")[0] for row in listed} == {path.stem}


def test_every_pinned_spec_operation_is_named_or_out_of_scope() -> None:
    spec = {operation(*row) for row in rows(FIXTURES / "spec.txt")}
    named = [operation(*row[:2]) for row in rows(FIXTURES / "operations.txt")]
    skipped = {operation(*row[:2]) for row in rows(FIXTURES / "out_of_scope.txt")}
    assert spec - set(named) - skipped == set()
    assert set(named) & skipped == set()


def test_messages_are_sent_edited_fetched_and_deleted_and_ids_fetched() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        guild_id = int(payloads.fixture("rest/guild")["id"])
        channel_id = int(payloads.channel()["id"])
        member_id = int(payloads.member()["user"]["id"])
        async with running(bot, fake):
            await asyncio.gather(
                bot.wait_for(GuildCreate),
                fake.dispatch("GUILD_CREATE", payloads.guild()),
            )
            messages = bot.rest.messages
            sent = await messages.create(
                channel_id, "hello", files=[File("a.txt", b"A")], reply_to=5
            )
            assert fake.requests[-1].json == {
                "content": "hello",
                "allowed_mentions": MENTIONS,
                "message_reference": {"type": 0, "message_id": 5},
                "attachments": [{"id": 0, "filename": "a.txt"}],
            }
            assert sent.content == "hello"
            assert sent.raw["id"] == str(sent.id)

            edited = await messages.edit(channel_id, sent.id, "edited", attachments=[7])
            assert fake.requests[-1].json == {
                "content": "edited",
                "allowed_mentions": MENTIONS,
                "attachments": [{"id": 7}],
            }
            fetched = await messages.fetch(channel_id, sent.id)
            assert (edited.content, fetched.content) == ("edited", "edited")
            link = f"https://discord.com/channels/{guild_id}/{channel_id}/{sent.id}"
            assert sent.jump_url == edited.jump_url == fetched.jump_url == link

            await messages.delete(channel_id, sent.id, reason="cleanup")
            assert fake.requests[-1].headers["x-audit-log-reason"] == "cleanup"
            with pytest.raises(NotFound):
                await messages.fetch(channel_id, sent.id)

            assert (await bot.rest.channels.fetch(channel_id)).id == channel_id
            assert (await bot.rest.guilds.fetch(guild_id)).id == guild_id
            assert (await bot.rest.users.fetch(member_id)).id == member_id
            member = await bot.rest.members.fetch(guild_id, member_id)
            assert member.id == member_id

    run(scenario)


@pytest.mark.parametrize(
    ("method", "call", "accepted"),
    [
        ("POST", lambda bot: bot.rest.messages.create(1, "hi"), "it took effect"),
        ("GET", lambda bot: bot.rest.users.fetch(1), "Discord answered the request"),
    ],
)
def test_a_rest_answer_that_fails_to_decode_raises_with_its_payload(
    method: str, call: Callable[[Bot], Awaitable[object]], accepted: str
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            fake.fail(method, "/", 200, {"id": "1"})
            with pytest.raises(UnexpectedResponse, match=accepted) as raised:
                await call(bot)
            assert raised.value.raw == {"id": "1"}

    run(scenario)
