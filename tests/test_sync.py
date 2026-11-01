import asyncio
import copy
import json
from collections.abc import Callable, Coroutine
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import pytest
from fakediscord import FakeDiscord

from bostie._bridge import Bridge, GatewayDelivery
from bostie.commands import Choice, Command, Group, Option, Range
from bostie.enums import Permissions
from bostie.errors import BadRequest, CommandSyncError
from bostie.http import HTTPClient
from bostie.models import Attachment, PartialChannel, Role, User
from bostie.router import Router
from bostie.sync import SyncResult, sync_commands

FIXTURES = Path(__file__).parent / "fixtures" / "models" / "rest"
REMOTE: list[dict[str, Any]] = json.loads((FIXTURES / "commands.json").read_text())

type Scenario = Callable[[FakeDiscord, HTTPClient], Coroutine[Any, Any, None]]


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
    where: Annotated[
        PartialChannel | None, "A channel", Option(channel_types=[2, 0])
    ] = None,
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
    """Search the bostie docs"""


COMMANDS: list[Command | Group] = [kit, search]


def ignore(delivery: GatewayDelivery, received: float) -> None:
    pass


def run(scenario: Scenario) -> None:
    async def main() -> None:
        async with FakeDiscord() as fake:
            bridge = Bridge(fake.token, fake.api_url, ignore, global_limit=50)
            async with bridge, asyncio.timeout(10):
                await scenario(fake, HTTPClient(bridge))

    asyncio.run(main())


def remote(**changes: dict[str, Any]) -> list[dict[str, Any]]:
    commands = copy.deepcopy(REMOTE)
    for command in commands:
        command.update(changes.get(command["name"], {}))
    return commands


def requests(fake: FakeDiscord) -> list[tuple[str, str]]:
    return [(request.method, request.path) for request in fake.requests]


def put_names(fake: FakeDiscord) -> list[str]:
    return [command["name"] for command in fake.requests[-1].json]


def test_matching_commands_send_only_the_get() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote()

        results = await sync_commands(http, fake.application_id, COMMANDS, ())

        assert results == [SyncResult(None, (), (), ())]
        assert requests(fake) == [
            ("GET", f"/applications/{fake.application_id}/commands")
        ]
        assert fake.requests[0].query == {"with_localizations": "true"}

    run(scenario)


def test_explicit_sync_puts_ours_then_other_types_verbatim() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote()

        await sync_commands(http, fake.application_id, COMMANDS, (), always_put=True)

        put = fake.requests[1]
        assert put.method == "PUT"
        assert put.json[:2] == [kit.payload("global"), search.payload("global")]
        user_info, launch = put.json[2:]
        assert user_info["id"] == "1544285690265731075"
        assert user_info["name_localizations"] == {"de": "Benutzerinfo"}
        assert launch["handler"] == 2
        for passthrough in (user_info, launch):
            assert not {"version", "application_id", "guild_id"} & passthrough.keys()

    run(scenario)


def test_pass_through_commands_drop_deprecated_keys_and_keep_their_settings() -> None:
    deprecated: dict[str, dict[str, Any]] = {
        "User Info": {
            "dm_permission": False,
            "contexts": None,
            "default_permission": False,
        },
        "launch": {"dm_permission": True, "default_permission": True},
    }

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote(**deprecated)

        await sync_commands(http, fake.application_id, COMMANDS, (), always_put=True)

        user_info, launch = fake.requests[1].json[2:]
        for passthrough in (user_info, launch):
            assert not {"dm_permission", "default_permission"} & passthrough.keys()
        assert (user_info["contexts"], user_info["default_member_permissions"]) == (
            [0],
            "0",
        )
        assert (launch["contexts"], launch["default_member_permissions"]) == (
            [0, 1, 2],
            None,
        )

    run(scenario)


def test_guild_pass_through_commands_drop_dm_permission_as_is() -> None:
    hidden_in_dms = {"guild_id": "11", "dm_permission": False, "contexts": None}

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.guild_commands["11"] = remote(**{"User Info": hidden_in_dms})

        await sync_commands(http, fake.application_id, COMMANDS, [11], always_put=True)

        user_info = fake.requests[1].json[2]
        assert "dm_permission" not in user_info
        assert user_info["contexts"] is None

    run(scenario)


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
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote(**changes)

        (result,) = await sync_commands(http, fake.application_id, COMMANDS, ())

        assert result.changed == changed
        assert [method for method, _ in requests(fake)] == ["GET", "PUT"]
        assert put_names(fake) == ["kit", "search", "User Info", "launch"]

    run(scenario)


def test_missing_and_extra_slash_commands_are_reported() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        stale = copy.deepcopy(REMOTE[1]) | {"name": "old"}
        fake.commands = [c for c in remote() if c["name"] != "kit"] + [stale]

        (result,) = await sync_commands(http, fake.application_id, COMMANDS, ())

        assert (result.added, result.removed, result.changed) == (
            ("kit",),
            ("old",),
            (),
        )
        assert put_names(fake) == ["kit", "search", "User Info", "launch"]

    run(scenario)


def test_no_local_commands_means_no_request() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        assert await sync_commands(http, fake.application_id, [], ()) == []
        assert fake.requests == []

    run(scenario)


def test_guild_scope_syncs_each_guild_and_ignores_contexts() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        results = await sync_commands(
            http, fake.application_id, COMMANDS, [11, 22], always_put=True
        )

        assert [result.guild_id for result in results] == [11, 22]
        assert requests(fake) == [
            (method, f"/applications/{fake.application_id}/guilds/{guild}/commands")
            for guild in (11, 22)
            for method in ("GET", "PUT")
        ]
        assert all("contexts" not in command for command in fake.requests[1].json)

    run(scenario)


def test_guild_scope_without_differences_sends_no_put() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.guild_commands["11"] = remote(kit={"contexts": [0]})

        await sync_commands(http, fake.application_id, COMMANDS, [11])

        assert [method for method, _ in requests(fake)] == ["GET"]

    run(scenario)


def test_a_400_names_the_command_option_and_function() -> None:
    error = json.loads((FIXTURES / "error_400_commands.json").read_bytes())

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote(kit={"description": "Old text"})
        route = f"/applications/{fake.application_id}/commands"
        fake.fail("PUT", route, 400, error)

        with pytest.raises(CommandSyncError) as raised:
            await sync_commands(http, fake.application_id, COMMANDS, ())

        assert str(raised.value) == (
            f"/kit inspect all ({kit_all.callback.__module__}.kit_all): "
            "option 'count': description: Must be between 1 and 100 in length."
        )
        assert raised.value.option_path == "option 'count'"
        assert isinstance(raised.value.__cause__, BadRequest)

    run(scenario)


def test_a_400_on_a_passthrough_command_names_it() -> None:
    errors = {"2": {"name": {"_errors": [{"code": "X", "message": "Bad name."}]}}}

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote(kit={"description": "Old text"})
        route = f"/applications/{fake.application_id}/commands"
        fake.fail("PUT", route, 400, {"code": 50035, "errors": errors})

        with pytest.raises(CommandSyncError, match=r"^/User Info: name: Bad name\.$"):
            await sync_commands(http, fake.application_id, COMMANDS, ())

    run(scenario)


def test_more_than_100_commands_warns() -> None:
    many = Router()

    async def handler(ctx: object) -> None:
        pass

    commands: list[Command | Group] = [
        many.command(name=f"c{index}")(handler) for index in range(101)
    ]

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        with pytest.warns(UserWarning, match="101 slash commands"):
            await sync_commands(http, fake.application_id, commands, ())

    run(scenario)
