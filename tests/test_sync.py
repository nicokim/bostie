import asyncio
import copy
import json
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Annotated, Any

import pytest

from bostie._bridge import Bridge, GatewayDelivery
from bostie.commands import Choice, Command, ContextMenuCommand, Group, Option, Range
from bostie.enums import InteractionContextType, Permissions
from bostie.errors import BadRequest, CommandSyncError
from bostie.http import HTTPClient
from bostie.models import Attachment, Message, PartialChannel, Role, User
from bostie.router import Router
from bostie.sync import SyncResult, sync_commands
from bostie.testing import FakeDiscord, payloads

FIXTURES = payloads.FIXTURES / "rest"
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
kit = router.group(
    "kit",
    "Exercise every option type",
    contexts=[InteractionContextType.GUILD, InteractionContextType.BOT_DM],
)
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


@router.command(
    contexts=[InteractionContextType.GUILD],
    default_permissions=Permissions.SEND_MESSAGES,
)
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
    """Search the bostie docs."""


@router.user_command("User Info", contexts=[InteractionContextType.GUILD])
async def user_info(ctx: object, user: User) -> None:
    pass


COMMANDS: list[Command | Group | ContextMenuCommand] = [kit, search, user_info]


def ignore(delivery: GatewayDelivery, received: float) -> None:
    pass


def run(scenario: Scenario) -> None:
    async def main() -> None:
        async with FakeDiscord() as fake:
            bridge = Bridge(fake.token, fake.api_url, ignore)
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
        assert put.json[:3] == [command.payload("global") for command in COMMANDS]
        (launch,) = put.json[3:]
        assert launch["id"] == "1329027302293635073"
        assert launch["handler"] == 2
        assert not {"version", "application_id", "guild_id"} & launch.keys()

    run(scenario)


@pytest.mark.parametrize(
    ("deprecated", "kept"),
    [
        (
            {"dm_permission": False, "contexts": None, "default_permission": False},
            ([0], "0"),
        ),
        ({"dm_permission": True, "default_permission": True}, ([0, 1, 2], None)),
    ],
)
def test_pass_through_commands_drop_deprecated_keys_and_keep_settings(
    deprecated: dict[str, Any], kept: tuple[list[int], str | None]
) -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote(launch=deprecated)

        await sync_commands(http, fake.application_id, COMMANDS, (), always_put=True)

        (launch,) = fake.requests[1].json[3:]
        assert not {"dm_permission", "default_permission"} & launch.keys()
        assert (launch["contexts"], launch["default_member_permissions"]) == kept

    run(scenario)


def test_guild_pass_through_commands_drop_dm_permission_as_is() -> None:
    hidden_in_dms = {"guild_id": "11", "dm_permission": False, "contexts": None}

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.guild_commands["11"] = remote(launch=hidden_in_dms)

        await sync_commands(http, fake.application_id, COMMANDS, [11], always_put=True)

        (launch,) = fake.requests[1].json[3:]
        assert "dm_permission" not in launch
        assert launch["contexts"] is None

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


def test_missing_and_extra_commands_are_matched_by_type_and_name() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        user_search = copy.deepcopy(REMOTE[2]) | {"name": "search"}
        fake.commands = [c for c in remote() if c["name"] != "kit"] + [user_search]

        (result,) = await sync_commands(http, fake.application_id, COMMANDS, ())

        assert (result.added, result.removed, result.changed) == (
            ("kit",),
            ("search",),
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


@pytest.mark.parametrize(
    ("index", "command"),
    [("2", f"User Info ({__name__}.user_info)"), ("3", "launch")],
)
def test_a_400_on_a_command_without_a_slash_names_it_as_is(
    index: str, command: str
) -> None:
    errors = {index: {"name": {"_errors": [{"code": "X", "message": "Bad name."}]}}}

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote(kit={"description": "Old text"})
        route = f"/applications/{fake.application_id}/commands"
        fake.fail("PUT", route, 400, {"code": 50035, "errors": errors})

        with pytest.raises(CommandSyncError) as raised:
            await sync_commands(http, fake.application_id, COMMANDS, ())

        assert str(raised.value) == f"{command}: name: Bad name."

    run(scenario)


def test_more_commands_of_a_type_than_discord_allows_warns() -> None:
    many = Router()

    async def handler(ctx: object) -> None:
        pass

    async def on_message(ctx: object, message: Message) -> None:
        pass

    commands: list[Command | Group | ContextMenuCommand] = [
        many.command(name=f"c{index}")(handler) for index in range(101)
    ]
    commands += [many.message_command(f"m{index}")(on_message) for index in range(16)]

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        with pytest.warns(UserWarning, match="commands; Discord allows") as warned:
            await sync_commands(http, fake.application_id, commands, ())

        assert [str(warning.message) for warning in warned] == [
            "101 slash commands; Discord allows 100 per scope",
            "16 message commands; Discord allows 15 per scope",
        ]

    run(scenario)
