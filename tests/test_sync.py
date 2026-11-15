import asyncio
import copy
import json
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Annotated, Any

import pytest

from bostie._bridge import Bridge, GatewayDelivery
from bostie._sync import SyncResult, sync_commands
from bostie.commands import Choice, Command, ContextMenuCommand, Group, Option, Range
from bostie.enums import InteractionContextType, Locale, Permissions
from bostie.errors import BadRequest, CommandSyncError
from bostie.http import HTTPClient
from bostie.models import Attachment, Message, PartialChannel, Role, User
from bostie.router import Router
from bostie.testing import FakeDiscord, payloads

FIXTURES = payloads.FIXTURES / "rest"
REMOTE: list[dict[str, Any]] = json.loads((FIXTURES / "commands.json").read_text())
INSTALL_TYPES = [0, 1]

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
    name_localizations={"de": "werkzeug"},
    description_localizations={"de": "Jeden Optionstyp ausprobieren"},
)
inspect_group = kit.group("inspect", "Inspect things")


@inspect_group.command(name="all", description="Use every option type")
async def kit_all(
    ctx: object,
    mode: Annotated[
        Mode,
        "How deep to look",
        Option(
            description_localizations={"de": "Wie gründlich gesucht wird"},
            choices=[
                Choice("Fast", "fast", name_localizations={"de": "Schnell"}),
                Choice("Thorough", "thorough", name_localizations={"de": "Gründlich"}),
            ],
        ),
    ],
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


@router.user_command(
    "User Info",
    contexts=[InteractionContextType.GUILD],
    name_localizations={"de": "Benutzerinfo"},
)
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

        results = await sync_commands(
            http, fake.application_id, COMMANDS, (), install_types=INSTALL_TYPES
        )

        assert results == [SyncResult(None, (), (), ())]
        assert requests(fake) == [
            ("GET", f"/applications/{fake.application_id}/commands")
        ]
        assert fake.requests[0].query == {"with_localizations": "true"}

    run(scenario)


def test_explicit_sync_puts_ours_then_other_types_verbatim() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote()

        await sync_commands(
            http,
            fake.application_id,
            COMMANDS,
            (),
            always_put=True,
            install_types=INSTALL_TYPES,
        )

        put = fake.requests[1]
        assert put.method == "PUT"
        assert put.json[:3] == [command._payload("global") for command in COMMANDS]
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

        await sync_commands(
            http,
            fake.application_id,
            COMMANDS,
            (),
            always_put=True,
            install_types=INSTALL_TYPES,
        )

        (launch,) = fake.requests[1].json[3:]
        assert not {"dm_permission", "default_permission"} & launch.keys()
        assert (launch["contexts"], launch["default_member_permissions"]) == kept

    run(scenario)


def test_guild_pass_through_commands_drop_dm_permission_as_is() -> None:
    hidden_in_dms = {"guild_id": "11", "dm_permission": False, "contexts": None}

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.guild_commands["11"] = remote(launch=hidden_in_dms)

        await sync_commands(
            http,
            fake.application_id,
            COMMANDS,
            [11],
            always_put=True,
            install_types=INSTALL_TYPES,
        )

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


def localized_category_choice() -> dict[str, Any]:
    options = copy.deepcopy(REMOTE[1]["options"])
    options[1]["choices"][0]["name_localizations"] = {"de": "Doku"}
    return {"options": options}


@pytest.mark.parametrize(
    ("changes", "changed"),
    [
        ({"search": swapped_search_options()}, ("search",)),
        ({"search": reversed_category_choices()}, ("search",)),
        ({"search": localized_category_choice()}, ("search",)),
        ({"search": {"name_localizations": {"fr": "chercher"}}}, ("search",)),
        ({"kit": {"description_localizations": None}}, ("kit",)),
        ({"kit": {"description": "Old text"}}, ("kit",)),
    ],
)
def test_a_difference_sends_one_put_with_the_other_types(
    changes: dict[str, dict[str, Any]], changed: tuple[str, ...]
) -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote(**changes)

        (result,) = await sync_commands(
            http, fake.application_id, COMMANDS, (), install_types=INSTALL_TYPES
        )

        assert result.changed == changed
        assert [method for method, _ in requests(fake)] == ["GET", "PUT"]
        assert put_names(fake) == ["kit", "search", "User Info", "launch"]

    run(scenario)


async def ping(ctx: object) -> None:
    """Check that the bot answers."""


@pytest.mark.parametrize(
    ("stored", "changed"),
    [
        ({}, ()),
        ({"contexts": [0]}, ("ping",)),
        ({"integration_types": [1]}, ("ping",)),
    ],
)
def test_global_commands_without_contexts_or_install_types_want_defaults(
    stored: dict[str, Any], changed: tuple[str, ...]
) -> None:
    command = Router().command()(ping)

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        await sync_commands(
            http, fake.application_id, [command], (), install_types=INSTALL_TYPES
        )
        fake.commands[0] |= stored

        (result,) = await sync_commands(
            http, fake.application_id, [command], (), install_types=INSTALL_TYPES
        )

        assert result.changed == changed

    run(scenario)


def test_missing_and_extra_commands_are_matched_by_type_and_name() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        user_search = copy.deepcopy(REMOTE[2]) | {"name": "search"}
        fake.commands = [c for c in remote() if c["name"] != "kit"] + [user_search]

        (result,) = await sync_commands(
            http, fake.application_id, COMMANDS, (), install_types=INSTALL_TYPES
        )

        assert (result.added, result.removed, result.changed) == (
            ("kit",),
            ("search",),
            (),
        )
        assert put_names(fake) == ["kit", "search", "User Info", "launch"]

    run(scenario)


def test_no_local_commands_means_no_request() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        assert (
            await sync_commands(
                http, fake.application_id, [], (), install_types=INSTALL_TYPES
            )
            == []
        )
        assert fake.requests == []

    run(scenario)


def test_guild_scope_syncs_each_guild() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        results = await sync_commands(
            http,
            fake.application_id,
            COMMANDS,
            [11, 22],
            always_put=True,
            install_types=INSTALL_TYPES,
        )

        assert [result.guild_id for result in results] == [11, 22]
        assert requests(fake) == [
            (method, f"/applications/{fake.application_id}/guilds/{guild}/commands")
            for guild in (11, 22)
            for method in ("GET", "PUT")
        ]

    run(scenario)


@pytest.mark.parametrize(
    ("dev_guilds", "scopes"), [((), [None, 33]), ((11,), [11, 33])]
)
def test_a_command_with_guilds_syncs_there_and_the_others_as_before(
    dev_guilds: tuple[int, ...], scopes: list[int | None]
) -> None:
    @Router().command(guilds=[33])
    async def admin(ctx: object) -> None:
        pass

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        results = await sync_commands(
            http,
            fake.application_id,
            [*COMMANDS, admin],
            dev_guilds,
            always_put=True,
            install_types=INSTALL_TYPES,
        )

        assert [result.guild_id for result in results] == scopes
        puts = [request.json for request in fake.requests if request.method == "PUT"]
        assert [[command["name"] for command in put] for put in puts] == [
            ["kit", "search", "User Info"],
            ["admin"],
        ]

    run(scenario)


def test_an_entry_point_takes_over_the_remote_one_by_id_and_stays_global() -> None:
    @Router().entry_point("Launch", description="Start the race")
    async def launch(ctx: object) -> None:
        pass

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote()

        (result,) = await sync_commands(
            http,
            fake.application_id,
            [*COMMANDS, launch],
            (),
            install_types=INSTALL_TYPES,
        )
        with pytest.warns(UserWarning, match="'Launch' is global"):
            await sync_commands(
                http,
                fake.application_id,
                [*COMMANDS, launch],
                [11],
                always_put=True,
                install_types=INSTALL_TYPES,
            )

        assert (result.added, result.removed) == (("Launch",), ("launch",))
        global_put = next(r.json for r in fake.requests if r.method == "PUT")
        assert global_put[-1] == {
            "id": "1329027302293635073",
            "type": 4,
            "name": "Launch",
            "description": "Start the race",
            "handler": 1,
            "default_member_permissions": None,
            "nsfw": False,
        }
        assert [command["name"] for command in fake.requests[-1].json] == [
            "kit",
            "search",
            "User Info",
        ]

    run(scenario)


def test_translate_fills_the_localizations_a_command_leaves_out() -> None:
    french = {"kit": "trousse", "Exercise every option type": "Tout essayer"}

    def translate(text: str, locale: Locale) -> str | None:
        if locale is Locale.GERMAN:
            return "nie"
        return french.get(text) if locale is Locale.FRENCH else None

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        await sync_commands(
            http,
            fake.application_id,
            COMMANDS,
            (),
            translate=translate,
            always_put=True,
            install_types=INSTALL_TYPES,
        )

        kit_payload = fake.requests[1].json[0]
        assert kit_payload["name_localizations"] == {"de": "werkzeug", "fr": "trousse"}
        assert kit_payload["description_localizations"]["fr"] == "Tout essayer"
        (inspect,) = kit_payload["options"]
        assert inspect["name_localizations"] == {"de": "nie"}
        search_payload = fake.requests[1].json[1]
        limit = next(o for o in search_payload["options"] if o["name"] == "limit")
        assert search_payload["name"] == "search"
        assert [choice["name_localizations"] for choice in limit["choices"]] == [
            {"de": "nie"},
            {"de": "nie"},
        ]

    run(scenario)


def test_guild_scope_without_differences_sends_no_put() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.guild_commands["11"] = remote(kit={"contexts": [0]})

        await sync_commands(
            http, fake.application_id, COMMANDS, [11], install_types=INSTALL_TYPES
        )

        assert [method for method, _ in requests(fake)] == ["GET"]

    run(scenario)


def test_a_400_names_the_command_option_and_function_of_each_error() -> None:
    error = json.loads((FIXTURES / "error_400_commands.json").read_bytes())
    error["errors"]["1"] = {
        "options": {
            "0": {"max_length": {"_errors": [{"code": "X", "message": "Too long."}]}}
        }
    }

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote(kit={"description": "Old text"})
        route = f"/applications/{fake.application_id}/commands"
        fake.fail("PUT", route, 400, error)

        with pytest.raises(CommandSyncError) as raised:
            await sync_commands(
                http, fake.application_id, COMMANDS, (), install_types=INSTALL_TYPES
            )

        assert str(raised.value).splitlines() == [
            (
                f"/kit inspect all ({kit_all.callback.__module__}.kit_all): "
                "option 'count': description: Must be between 1 and 100 in length."
            ),
            f"/search ({__name__}.search): option 'query': max_length: Too long.",
        ]
        assert [problem.option_path for problem in raised.value.problems] == [
            "option 'count'",
            "option 'query'",
        ]
        assert isinstance(raised.value.__cause__, BadRequest)

    run(scenario)


def test_a_400_whose_path_misses_the_commands_names_what_it_can() -> None:
    errors = {
        "9": {"name": {"_errors": [{"code": "X", "message": "Bad name."}]}},
        "0": {
            "options": {
                "7": {"description": {"_errors": [{"code": "X", "message": "Long."}]}}
            }
        },
    }

    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.commands = remote(kit={"description": "Old text"})
        route = f"/applications/{fake.application_id}/commands"
        fake.fail("PUT", route, 400, {"code": 50035, "errors": errors})

        with pytest.raises(CommandSyncError) as raised:
            await sync_commands(
                http, fake.application_id, COMMANDS, (), install_types=INSTALL_TYPES
            )

        assert str(raised.value).splitlines() == [
            "9.name: Bad name.",
            "/kit: options.7.description: Long.",
        ]

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
            await sync_commands(
                http, fake.application_id, COMMANDS, (), install_types=INSTALL_TYPES
            )

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
            await sync_commands(
                http, fake.application_id, commands, (), install_types=INSTALL_TYPES
            )

        assert [str(warning.message) for warning in warned] == [
            "101 slash commands; Discord allows 100 per scope",
            "16 message commands; Discord allows 15 per scope",
        ]

    run(scenario)
