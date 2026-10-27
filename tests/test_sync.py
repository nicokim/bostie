import asyncio
import copy
import json
from collections.abc import Coroutine
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ClassVar

import pytest

from lyrecord.commands import Choice, Command, Group, Option, Range
from lyrecord.enums import Permissions
from lyrecord.errors import BadRequest, CommandSyncError
from lyrecord.router import Router
from lyrecord.sync import SyncResult, sync_commands

FIXTURES = Path(__file__).parent / "fixtures" / "models" / "rest"
REMOTE: list[dict[str, Any]] = json.loads((FIXTURES / "commands.json").read_text())
APPLICATION_ID = 1329027298099331073
GLOBAL_ROUTE = "/applications/{application_id}/commands"
GUILD_ROUTE = "/applications/{application_id}/guilds/{guild_id}/commands"


class User:
    __option_type__: ClassVar[int] = 6


class Channel:
    __option_type__: ClassVar[int] = 7


class Role:
    __option_type__: ClassVar[int] = 8


class Attachment:
    __option_type__: ClassVar[int] = 11


class Mode(Enum):
    Fast = "fast"
    Thorough = "thorough"


class Category(Enum):
    Docs = "docs"
    Issues = "issues"


async def suggest(ctx: object, value: object) -> list[str]:
    return []


router = Router()
kit = router.group("kit", "Exercise every option type")
inspect_group = kit.group("inspect", "Inspect things")


@inspect_group.command(name="all", description="Use every option type")
async def kit_all(
    ctx: object,
    mode: Annotated[Mode, "How deep to look"],
    count: Annotated[Range[int, 1, 10], "How many times"],
    ratio: Annotated[Range[float, 0.0, 1.0] | None, "A fraction"] = None,
    loud: Annotated[bool | None, "Announce the result"] = None,
    target: Annotated[User | None, "A user"] = None,
    where: Annotated[Channel | None, "A channel", Option(channel_types=[2, 0])] = None,
    role: Annotated[Role | None, "A role"] = None,
    who: Annotated[User | Role | None, "A user or role"] = None,
    file: Annotated[Attachment | None, "An attachment"] = None,
) -> None:
    pass


@router.command(guild_only=True, default_permissions=Permissions.SEND_MESSAGES)
async def search(
    ctx: object,
    query: Annotated[
        Range[str, 2, 100], "What to look for", Option(autocomplete=suggest)
    ],
    category: Annotated[Category, "Where to look"],
    limit: Annotated[
        int | None,
        "Maximum results",
        Option(choices=[Choice("Five", 5), Choice("Ten", 10)]),
    ] = None,
) -> None:
    """Search the lyrecord docs"""


COMMANDS: list[Command | Group] = [kit, search]


@dataclass
class Call:
    method: str
    route: str
    json: Any
    query: Any
    params: dict[str, object]


class FakeHTTP:
    def __init__(
        self, remote: list[dict[str, Any]], error: Exception | None = None
    ) -> None:
        self.remote = remote
        self.error = error
        self.calls: list[Call] = []

    async def request(
        self,
        method: str,
        route: str,
        /,
        *,
        json: Any = None,
        files: Any = (),
        query: Any = None,
        reason: str | None = None,
        **params: object,
    ) -> Any:
        self.calls.append(Call(method, route, json, query, params))
        if method == "GET":
            return copy.deepcopy(self.remote)
        if self.error is not None:
            raise self.error
        return json


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def remote(**changes: dict[str, Any]) -> list[dict[str, Any]]:
    commands = copy.deepcopy(REMOTE)
    for command in commands:
        command.update(changes.get(command["name"], {}))
    return commands


def methods(http: FakeHTTP) -> list[str]:
    return [call.method for call in http.calls]


def test_matching_commands_send_only_the_get() -> None:
    http = FakeHTTP(remote())

    (result,) = run(sync_commands(http, APPLICATION_ID, COMMANDS, ()))

    assert result == SyncResult(None, (), (), ())
    assert http.calls == [
        Call(
            "GET",
            GLOBAL_ROUTE,
            None,
            {"with_localizations": True},
            {"application_id": APPLICATION_ID},
        )
    ]


def test_explicit_sync_puts_ours_then_other_types_verbatim() -> None:
    http = FakeHTTP(remote())

    run(sync_commands(http, APPLICATION_ID, COMMANDS, (), always_put=True))

    put = http.calls[1]
    assert (put.method, put.route) == ("PUT", GLOBAL_ROUTE)
    assert put.json[:2] == [kit.payload("global"), search.payload("global")]
    user_info, launch = put.json[2:]
    assert user_info["id"] == "1544285690265731075"
    assert user_info["name_localizations"] == {"de": "Benutzerinfo"}
    assert launch["handler"] == 2
    for passthrough in (user_info, launch):
        assert not {"version", "application_id", "guild_id"} & passthrough.keys()


def swapped_search_options() -> dict[str, Any]:
    query, category, limit = REMOTE[1]["options"]
    return {"options": [category, query, limit]}


def reversed_category_choices() -> dict[str, Any]:
    options = copy.deepcopy(REMOTE[1]["options"])
    options[1]["choices"].reverse()
    return {"options": options}


@pytest.mark.parametrize(
    ("changes", "changed"),
    [
        ({"search": swapped_search_options()}, ("search",)),
        ({"search": reversed_category_choices()}, ("search",)),
        ({"kit": {"description": "Old text"}}, ("kit",)),
        ({"kit": {"contexts": [0]}}, ("kit",)),
        ({"search": {"default_member_permissions": None}}, ("search",)),
        ({"search": {"nsfw": True}}, ("search",)),
    ],
)
def test_a_difference_sends_one_put_with_the_other_types(
    changes: dict[str, dict[str, Any]], changed: tuple[str, ...]
) -> None:
    http = FakeHTTP(remote(**changes))

    (result,) = run(sync_commands(http, APPLICATION_ID, COMMANDS, ()))

    assert result.changed == changed
    assert methods(http) == ["GET", "PUT"]
    assert [command["name"] for command in http.calls[1].json] == [
        "kit",
        "search",
        "User Info",
        "launch",
    ]


def test_missing_and_extra_slash_commands_are_reported() -> None:
    stale = copy.deepcopy(REMOTE[1]) | {"name": "old"}
    http = FakeHTTP(
        [command for command in REMOTE if command["name"] != "kit"] + [stale]
    )

    (result,) = run(sync_commands(http, APPLICATION_ID, COMMANDS, ()))

    assert (result.added, result.removed, result.changed) == (("kit",), ("old",), ())
    assert [command["name"] for command in http.calls[1].json] == [
        "kit",
        "search",
        "User Info",
        "launch",
    ]


def test_no_local_commands_means_no_request() -> None:
    http = FakeHTTP(remote())

    assert run(sync_commands(http, APPLICATION_ID, [], ())) == []
    assert http.calls == []


def test_guild_scope_syncs_each_guild_and_ignores_contexts() -> None:
    http = FakeHTTP(remote())

    results = run(
        sync_commands(http, APPLICATION_ID, COMMANDS, [11, 22], always_put=True)
    )

    assert [result.guild_id for result in results] == [11, 22]
    assert [(call.method, call.route, call.params) for call in http.calls] == [
        (method, GUILD_ROUTE, {"application_id": APPLICATION_ID, "guild_id": guild})
        for guild in (11, 22)
        for method in ("GET", "PUT")
    ]
    assert all("contexts" not in command for command in http.calls[1].json[:2])


def test_guild_scope_without_differences_sends_no_put() -> None:
    http = FakeHTTP(remote(kit={"contexts": [0]}))

    run(sync_commands(http, APPLICATION_ID, COMMANDS, [11]))

    assert methods(http) == ["GET"]


def test_a_400_names_the_command_option_and_function() -> None:
    error_body = (FIXTURES / "error_400_commands.json").read_bytes()
    error_json = json.loads(error_body)
    error = BadRequest(
        400, 50035, "Invalid Form Body", error_json["errors"], error_body
    )
    http = FakeHTTP(remote(kit={"description": "Old text"}), error)

    with pytest.raises(CommandSyncError) as raised:
        run(sync_commands(http, APPLICATION_ID, COMMANDS, ()))

    assert str(raised.value) == (
        f"/kit inspect all ({kit_all.callback.__module__}.kit_all): "
        "option 'count': description: Must be between 1 and 100 in length."
    )
    assert raised.value.option_path == "option 'count'"
    assert raised.value.__cause__ is error


def test_a_400_on_a_passthrough_command_names_it() -> None:
    errors = {"2": {"name": {"_errors": [{"code": "X", "message": "Bad name."}]}}}
    error = BadRequest(400, 50035, "Invalid Form Body", errors, b"")
    http = FakeHTTP(remote(kit={"description": "Old text"}), error)

    with pytest.raises(CommandSyncError, match=r"^/User Info: name: Bad name\.$"):
        run(sync_commands(http, APPLICATION_ID, COMMANDS, ()))


def test_more_than_100_commands_warns() -> None:
    many = Router()

    async def handler(ctx: object) -> None:
        pass

    commands: list[Command | Group] = [
        many.command(name=f"c{index}")(handler) for index in range(101)
    ]
    http = FakeHTTP([])

    with pytest.warns(UserWarning, match="101 slash commands"):
        run(sync_commands(http, APPLICATION_ID, commands, ()))
