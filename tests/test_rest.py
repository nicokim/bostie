import ast
import asyncio
import contextlib
import inspect
import re
import textwrap
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote

import pytest

from bostie import Bot, File, HTTPException, NotFound, UnexpectedResponse
from bostie.cache import Cache
from bostie.enums import (
    AutoModerationEventType,
    AutoModerationTriggerType,
    OverwriteType,
    ScheduledEventEntityType,
    ScheduledEventPrivacyLevel,
)
from bostie.events import GuildCreate
from bostie.models import AllowedMentions
from bostie.rest import REST, BearerREST
from bostie.rest.messages import MessageList
from bostie.testing import FakeDiscord, payloads, run, running

if TYPE_CHECKING:
    from bostie.http import HTTPClient

FIXTURES = Path(__file__).parent / "fixtures" / "rest"
MENTIONS = {"parse": ["users", "roles"]}
VERBS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

type Shape = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]


PNG = File("a.png", b"\x89PNG\r\n\x1a\n")
REQUIRED: dict[str, dict[str, Any]] = {
    "auto_moderation.create": {
        "name": "n",
        "event_type": AutoModerationEventType.MESSAGE_SEND,
        "trigger_type": AutoModerationTriggerType.KEYWORD,
        "actions": [],
    },
    "bans.bulk_create": {"user_ids": [1]},
    "channels.create": {"name": "n"},
    "channels.edit_permissions": {"type": OverwriteType.ROLE},
    "channels.edit_positions": {"positions": [{"id": 1}]},
    "channels.follow": {"webhook_channel_id": 1},
    "channels.set_voice_status": {"status": None},
    "emojis.create": {"name": "n", "image": PNG},
    "messages.bulk_delete": {"messages": [1, 2]},
    "roles.edit_positions": {"positions": {1: 1}},
    "scheduled_events.create": {
        "name": "n",
        "scheduled_start_time": datetime(2026, 11, 1, tzinfo=UTC),
        "privacy_level": ScheduledEventPrivacyLevel.GUILD_ONLY,
        "entity_type": ScheduledEventEntityType.VOICE,
    },
    "soundboard.create": {"name": "n", "sound": File("a.mp3", b"ID3")},
    "stage_instances.create": {"channel_id": 1, "topic": "t"},
    "stickers.create": {"name": "n", "tags": "t", "file": PNG},
    "threads.start": {"name": "n"},
    "threads.start_from_message": {"name": "n"},
    "threads.start_in_forum": {"name": "n", "message": {"content": "c"}},
    "webhooks.create": {"name": "n"},
}


def rows(path: Path) -> list[list[str]]:
    lines = path.read_text().splitlines()
    return [line.split() for line in lines if not line.startswith("#")]


def pending_files() -> list[Path]:
    return sorted((FIXTURES / "pending").glob("*.txt"))


def operation(verb: str, path: str) -> tuple[str, str]:
    return verb, re.sub(r"\{\w+\}", "{}", path)


def implemented(rest: REST | BearerREST) -> dict[str, Shape]:
    found: dict[str, Shape] = {}
    for group_name, group in vars(rest).items():
        for name, function in inspect.getmembers(type(group), inspect.isfunction):
            if name.startswith("_"):
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


@pytest.mark.parametrize(("surface", "bearer"), [(REST, False), (BearerREST, True)])
def test_the_rest_groups_implement_every_table_row_not_pending(
    surface: type[REST | BearerREST], bearer: bool
) -> None:
    table = [
        row
        for row in rows(FIXTURES / "operations.txt")
        if ("bearer" in row[3:]) == bearer
    ]
    pending = {row[2] for path in pending_files() for row in rows(path)}
    rest = surface(cast("HTTPClient", None), AllowedMentions(), Cache())

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


def test_every_operation_that_takes_a_reason_sends_it_to_the_audit_log() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            unsent = []
            for verb, path, name, *marks in rows(FIXTURES / "operations.txt"):
                if "reason" not in marks:
                    continue
                group, method = name.split(".")
                call = getattr(getattr(bot.rest, group), method)
                with contextlib.suppress(HTTPException, UnexpectedResponse):
                    await call(
                        *[1] * path.count("{"), **REQUIRED.get(name, {}), reason=name
                    )
                request = fake.requests[-1]
                reason = unquote(request.headers.get("x-audit-log-reason", ""))
                if (request.method, reason) != (verb, name):
                    unsent.append(name)

            assert unsent == []

    run(scenario)


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

            edited = await messages.edit(
                channel_id, sent.id, content="edited", attachments=[7]
            )
            assert fake.requests[-1].json == {
                "content": "edited",
                "allowed_mentions": MENTIONS,
                "attachments": [{"id": 7}],
            }
            fetched = await messages.fetch(channel_id, sent.id)
            assert (edited.content, fetched.content) == ("edited", "edited")
            link = f"https://discord.com/channels/{guild_id}/{channel_id}/{sent.id}"
            assert sent.jump_url == edited.jump_url == fetched.jump_url == link
            cleared = await messages.edit(channel_id, sent.id, content=None)
            assert cleared.content == ""

            await messages.delete(channel_id, sent.id)
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


def history(channel_id: int, ids: range) -> list[dict[str, Any]]:
    message = payloads.fixture("rest/message") | {"channel_id": str(channel_id)}
    return [message | {"id": str(id)} for id in ids]


@pytest.mark.parametrize(
    ("options", "pages", "walked", "queries"),
    [
        (
            {"limit": 150},
            [range(300, 200, -1), range(200, 150, -1)],
            range(300, 150, -1),
            [{"limit": "100"}, {"limit": "50", "before": "201"}],
        ),
        (
            {"after": 100},
            [range(200, 100, -1), range(230, 200, -1)],
            range(101, 231),
            [{"limit": "100", "after": "100"}, {"limit": "100", "after": "200"}],
        ),
        (
            {"around": 50, "limit": 500},
            [range(60, 40, -1)],
            range(60, 40, -1),
            [{"limit": "100", "around": "50"}],
        ),
        (
            {"after": datetime(2016, 4, 30, 11, 18, 25, 796000, tzinfo=UTC)},
            [range(5, 4, -1)],
            range(5, 6),
            [{"limit": "100", "after": "175928847303180287"}],
        ),
    ],
)
def test_a_listing_walks_pages_from_its_cursor_up_to_its_limit(
    options: MessageList,
    pages: list[range],
    walked: range,
    queries: list[dict[str, str]],
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        channel_id = int(payloads.channel()["id"])
        async with running(bot, fake):
            route = f"/channels/{channel_id}/messages"
            for ids in pages:
                fake.fail("GET", route, 200, history(channel_id, ids))
            listed = bot.rest.messages.list(channel_id, **options)
            assert [(message.id, message.raw["id"]) async for message in listed] == [
                (id, str(id)) for id in walked
            ]
            assert [request.query for request in fake.requests[-len(pages) :]] == (
                queries
            )

    run(scenario)


def test_a_listing_refuses_unknown_and_mutually_exclusive_cursors() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            sent = len(fake.requests)
            unknown: dict[str, Any] = {"since": 2}
            with pytest.raises(TypeError, match="unexpected options: since"):
                bot.rest.messages.list(1, **unknown)
            with pytest.raises(ValueError, match="before and around are mutually"):
                bot.rest.messages.list(1, before=2, around=3)
            assert len(fake.requests) == sent

    run(scenario)


def test_options_send_none_as_null_leave_out_the_rest_and_inline_files() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            fake.fail("PATCH", "/users/@me", 200, fake.bot_user)
            users = bot.rest.users
            me = await users.edit_current(avatar=None, banner=File("b.PNG", b"PNG"))
            assert fake.requests[-1].json == {
                "avatar": None,
                "banner": "data:image/png;base64,UE5H",
            }
            assert me.id == int(fake.bot_user["id"])
            with pytest.raises(ValueError, match="must end in one of"):
                await users.edit_current(banner=File("b.bmp", b"BM"))

    run(scenario)
