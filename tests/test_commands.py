import inspect
import re
from enum import Enum, IntEnum
from typing import Annotated, Any, Literal

import msgspec
import pytest
from fakediscord import payloads

from bostie.commands import (
    Choice,
    Command,
    Option,
    Range,
    autocomplete_choices,
)
from bostie.enums import ChannelType, OptionType, Permissions
from bostie.errors import CommandDefinitionError, OptionConversionError
from bostie.models import (
    Attachment,
    Channel,
    InteractionDataOption,
    Member,
    PartialChannel,
    PartialUser,
    Resolved,
    Role,
    User,
    decode,
)
from bostie.router import Router

RESOLVED = decode(Resolved, msgspec.json.encode(payloads.resolved()))
USER_ID = 820774541721731073
ROLE_ID = 1335174302269571073
CHANNEL_ID = 1335173547294851073
ATTACHMENT_ID = 1564609863064092673


def filled(name: str, type: int, value: str | float | bool) -> InteractionDataOption:
    return InteractionDataOption(name=name, type=OptionType(type), value=value)


class Unit(Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class Size(IntEnum):
    SMALL = 1
    LARGE = 2


async def suggest(ctx: object, value: object) -> list[str]:
    return []


def command_with(hint: object, default: object = inspect.Parameter.empty) -> Command:
    async def handler(ctx: object, value: Any) -> None:
        pass

    handler.__annotations__ = {"value": hint}
    if default is not inspect.Parameter.empty:
        handler.__defaults__ = (default,)
    return Router().command(handler)


def only_option(command: Command) -> dict[str, Any]:
    (option,) = command.payload("guild")["options"]
    return dict(option)


def option(type: int, **fields: object) -> dict[str, object]:
    return {
        "type": type,
        "name": "value",
        "description": "\N{HORIZONTAL ELLIPSIS}",
        "required": True,
    } | fields


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        (str, option(3)),
        (int, option(4)),
        (float, option(10)),
        (bool, option(5)),
        (User, option(6)),
        (Member, option(6)),
        (Role, option(8)),
        (PartialChannel, option(7)),
        (Attachment, option(11)),
        (User | Role, option(9)),
        (Member | Role, option(9)),
        (
            Literal["a", "b"],
            option(
                3, choices=[{"name": "a", "value": "a"}, {"name": "b", "value": "b"}]
            ),
        ),
        (
            Literal[1, 2],
            option(4, choices=[{"name": "1", "value": 1}, {"name": "2", "value": 2}]),
        ),
        (
            Literal[0.5, 1.5],
            option(
                10,
                choices=[{"name": "0.5", "value": 0.5}, {"name": "1.5", "value": 1.5}],
            ),
        ),
        (
            Unit,
            option(
                3,
                choices=[
                    {"name": "METRIC", "value": "metric"},
                    {"name": "IMPERIAL", "value": "imperial"},
                ],
            ),
        ),
        (
            Size,
            option(
                4,
                choices=[{"name": "SMALL", "value": 1}, {"name": "LARGE", "value": 2}],
            ),
        ),
        (Range[int, 1, 5], option(4, min_value=1, max_value=5)),
        (Range[float, 0.5, None], option(10, min_value=0.5)),
        (Range[str, 2, 10], option(3, min_length=2, max_length=10)),
        (Range[int, 0, 10], option(4, min_value=0, max_value=10)),
        (Range[str, 0, 6000], option(3, min_length=0, max_length=6000)),
        (int | None, option(4, required=False)),
        (User | Role | None, option(9, required=False)),
        (
            Annotated[int, "How many"] | None,
            option(4, required=False, description="How many"),
        ),
        (
            Annotated[int | None, Option(min=1, max=10)],
            option(4, required=False, min_value=1, max_value=10),
        ),
        (Annotated[int, "How many"], option(4, description="How many")),
        (Annotated[str, Option(autocomplete=suggest)], option(3, autocomplete=True)),
        (
            Annotated[
                PartialChannel,
                Option(channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_VOICE]),
            ],
            option(7, channel_types=[0, 2]),
        ),
        (
            Annotated[int, Option(choices=[Choice("Five", 5), 10])],
            option(
                4,
                choices=[{"name": "Five", "value": 5}, {"name": "10", "value": 10}],
            ),
        ),
        (
            Annotated[float, Option(choices=[1, 2.5])],
            option(
                10,
                choices=[{"name": "1", "value": 1}, {"name": "2.5", "value": 2.5}],
            ),
        ),
        (
            Annotated[int, Option(extra={"min_value": 3, "future": True})],
            option(4, min_value=3, future=True),
        ),
    ],
)
def test_hint_becomes_option_json(hint: object, expected: dict[str, object]) -> None:
    assert only_option(command_with(hint)) == expected


def test_a_default_makes_the_option_optional() -> None:
    assert only_option(command_with(int, 6))["required"] is False


def test_required_options_come_first_in_declaration_order() -> None:
    router = Router()

    @router.command
    async def roll(
        ctx: object, a: int = 1, *, b: str, c: bool | None, d: float
    ) -> None:
        pass

    names = [option["name"] for option in roll.payload("guild")["options"]]
    assert names == ["b", "d", "a", "c"]


def test_option_settings_win_over_range_and_name() -> None:
    hint = Annotated[Range[int, 1, 10], Option(name="dice", min=2)]

    assert only_option(command_with(hint)) == option(
        4, name="dice", min_value=2, max_value=10
    )


def test_annotated_text_wins_over_option_description() -> None:
    hint = Annotated[int, Option(description="from option"), "from text"]

    assert only_option(command_with(hint))["description"] == "from text"


def test_command_name_and_description_come_from_the_function() -> None:
    router = Router()

    @router.command
    async def roll(ctx: object) -> None:
        """Roll some dice.

        More text that Discord never sees.
        """

    @router.command(name="bostie-check", description="Explicit")
    async def check(ctx: object) -> None:
        """Ignored."""

    @router.command
    async def bare(ctx: object) -> None:
        pass

    assert (roll.name, roll.description) == ("roll", "Roll some dice.")
    assert (check.name, check.description) == ("bostie-check", "Explicit")
    assert bare.description == "\N{HORIZONTAL ELLIPSIS}"


@pytest.mark.parametrize(
    "name",
    [
        "roll",
        "a-b_c9",
        "\N{MODIFIER LETTER APOSTROPHE}ok",
        "नमस्ते",
        "สวัสดี",
        "ß",
        "x" * 32,
    ],
)
def test_valid_names(name: str) -> None:
    async def handler(ctx: object) -> None:
        pass

    assert Router().command(name=name)(handler).name == name


@pytest.mark.parametrize("name", ["Roll", "x" * 33, "", "a b", "a'b"])
def test_invalid_names(name: str) -> None:
    async def handler(ctx: object) -> None:
        pass

    with pytest.raises(CommandDefinitionError, match="not a valid name"):
        Router().command(name=name)(handler)


async def autocomplete(ctx: object, value: object) -> list[str]:
    return []


@pytest.mark.parametrize(
    ("hint", "problem"),
    [
        (list[int], "unsupported type hint list\\[int\\]"),
        (int | str, "unsupported type hint"),
        (User | Member, "unsupported type hint"),
        (Channel, "Channel; a channel option is a PartialChannel"),
        (PartialUser | None, "PartialUser; a user option is a User or a Member"),
        (Literal["a", 1], "must be all str, all int or all float"),
        pytest.param(
            Literal[True], "must be all str, all int or all float", id="Literal-bool"
        ),
        (
            Annotated[Literal["a"], Option(autocomplete=autocomplete)],
            "autocomplete cannot be combined with choices",
        ),
        (
            Annotated[bool, Option(autocomplete=autocomplete)],
            "autocomplete does not apply to BOOLEAN",
        ),
        (Literal[tuple(range(26))], "26 choices"),
        (
            Annotated[str, Option(choices=[Choice("x" * 101, "a")])],
            "choice name 'x+' must be 1-100",
        ),
        (Annotated[str, Option(choices=[Choice("", "a")])], "choice name '' must be"),
        (
            Annotated[str, Option(choices=[Choice("long", "x" * 101)])],
            "longer than 100",
        ),
        (Annotated[str, Option(choices=[1])], "int value for a STRING option"),
        (Annotated[Literal["a"], Option(choices=["b"])], "already sets the choices"),
        (Annotated[int, Option(name="Bad")], "not a valid name"),
        (Annotated[int, "x" * 101], "description 'x+' must be 1-100"),
        (Annotated[int, Option(description="")], "description '' must be"),
        (Range[int, 5, 1], "min_value is greater than max_value"),
        (Range[str, 5, 1], "min_length is greater than max_length"),
        (Range[str, -1, 5], "min_length must be 0-6000"),
        (Range[str, 1, 6001], "max_length must be 1-6000"),
        pytest.param(
            Annotated[str, Option(max_length=0)],
            "max_length must be 1-6000",
            id="Annotated-max_length 0",
        ),
        (Range[float, -(2**53) - 1, 0], "within -9007199254740992..9007199254740992"),
        (Range[int, 0, 2**53], "within -9007199254740991..9007199254740991"),
        (Range[bool, 0, 1], "min_value does not apply to BOOLEAN"),
        (Annotated[str, Option(min=1)], "min_value does not apply to STRING"),
        (
            Annotated[int, Option(channel_types=[ChannelType.GUILD_TEXT])],
            "channel_types does not apply",
        ),
    ],
)
def test_definition_errors_name_the_function_and_parameter(
    hint: object, problem: str
) -> None:
    with pytest.raises(CommandDefinitionError) as raised:
        command_with(hint)

    message = str(raised.value)
    assert message.startswith(
        "test_commands.command_with.<locals>.handler: parameter 'value': "
    )
    assert re.search(problem, message)


def test_range_float_accepts_the_number_limit() -> None:
    assert (
        only_option(command_with(Range[float, -(2**53), 2**53]))["max_value"] == 2**53
    )


async def no_context() -> None:
    pass


async def var_args(ctx: object, *values: int) -> None:
    pass


async def var_kwargs(ctx: object, **values: int) -> None:
    pass


async def positional_only(ctx: object, value: int, /) -> None:
    pass


async def unannotated(ctx: object, value: int) -> None:
    pass


unannotated.__annotations__.clear()


async def duplicate(ctx: object, a: int, b: Annotated[int, Option(name="a")]) -> None:
    pass


@pytest.mark.parametrize(
    ("callback", "problem"),
    [
        (no_context, "the first parameter must take the context"),
        (var_args, "parameter 'values': options cannot be \\*args"),
        (var_kwargs, "parameter 'values': options cannot be"),
        (positional_only, "parameter 'value': options cannot be"),
        (unannotated, "parameter 'value': options need a type hint"),
        (duplicate, "two options are named 'a'"),
    ],
)
def test_parameter_list_errors(callback: Any, problem: str) -> None:
    with pytest.raises(CommandDefinitionError, match=f"test_commands.*: {problem}"):
        Router().command(callback)


def test_more_than_25_options_is_an_error() -> None:
    async def handler(ctx: object, **options: int) -> None:
        pass

    parameters = [inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    parameters += [
        inspect.Parameter(f"o{i}", inspect.Parameter.KEYWORD_ONLY, annotation=int)
        for i in range(26)
    ]
    vars(handler)["__signature__"] = inspect.Signature(parameters)
    handler.__annotations__ = {f"o{i}": int for i in range(26)}

    with pytest.raises(CommandDefinitionError, match="26 options; Discord allows 25"):
        Router().command(handler)


def test_text_over_8000_characters_warns() -> None:
    names = tuple("x" * 95 + f"{i:05}" for i in range(25))

    async def handler(ctx: object, a: Any, b: Any) -> None:
        pass

    many: object = Literal[names]
    handler.__annotations__ = {"a": many, "b": many}

    with pytest.warns(UserWarning, match="/handler uses 10012 characters"):
        Router().command(handler)


def test_global_scope_always_sets_contexts() -> None:
    router = Router()

    @router.command
    async def anywhere(ctx: object) -> None:
        pass

    @router.command(guild_only=True)
    async def guilds(ctx: object) -> None:
        pass

    @router.command(extra={"contexts": [0, 1, 2], "integration_types": [0, 1]})
    async def installed(ctx: object) -> None:
        pass

    assert anywhere.payload("global")["contexts"] == [0, 1]
    assert guilds.payload("global")["contexts"] == [0]
    assert "contexts" not in guilds.payload("guild")
    assert installed.payload("global")["contexts"] == [0, 1, 2]
    assert installed.payload("guild")["integration_types"] == [0, 1]


def test_command_payload() -> None:
    router = Router()

    @router.command(default_permissions=Permissions.MANAGE_ROLES, nsfw=True)
    async def tag(ctx: object, name: str) -> None:
        """Show a tag."""

    assert tag.payload("global") == {
        "type": 1,
        "name": "tag",
        "description": "Show a tag.",
        "options": [option(3, name="name")],
        "default_member_permissions": "268435456",
        "nsfw": True,
        "contexts": [0, 1],
    }


def test_group_tree_payload_and_names() -> None:
    router = Router()
    role = router.group(
        "role",
        "Manage roles",
        default_permissions=Permissions.MANAGE_ROLES,
        guild_only=True,
    )
    color = role.group("color", "Color roles")

    @color.command(name="set")
    async def color_set(ctx: object, member: Member, code: Range[str, 7, 7]) -> None:
        """Give a member a color role."""

    @role.command
    async def remove(ctx: object) -> None:
        """Remove a role."""

    assert color_set.qualified_name == "role color set"
    assert role.payload("global") == {
        "type": 1,
        "name": "role",
        "description": "Manage roles",
        "options": [
            {
                "type": 2,
                "name": "color",
                "description": "Color roles",
                "options": [
                    {
                        "type": 1,
                        "name": "set",
                        "description": "Give a member a color role.",
                        "options": [
                            option(6, name="member"),
                            option(3, name="code", min_length=7, max_length=7),
                        ],
                    }
                ],
            },
            {
                "type": 1,
                "name": "remove",
                "description": "Remove a role.",
                "options": [],
            },
        ],
        "default_member_permissions": "268435456",
        "nsfw": False,
        "contexts": [0],
    }


def test_groups_nest_one_level_and_names_stay_unique() -> None:
    router = Router()
    role = router.group("role", "Manage roles")
    color = role.group("color", "Color roles")

    with pytest.raises(CommandDefinitionError, match="one level deep"):
        color.group("shade", "Too deep")
    with pytest.raises(CommandDefinitionError, match="already has a subcommand"):
        role.group("color", "Again")


@pytest.mark.parametrize("setting", ["default_permissions", "guild_only", "nsfw"])
def test_subcommands_cannot_set_top_level_settings(setting: str) -> None:
    group = Router().group("admin", "Admin tools")
    settings: dict[str, Any] = {setting: Permissions.ADMINISTRATOR}

    async def handler(ctx: object) -> None:
        pass

    with pytest.raises(TypeError, match=setting):
        group.command(**settings)(handler)


def test_a_group_holds_at_most_25_subcommands() -> None:
    group = Router().group("many", "Many subcommands")
    for index in range(25):
        group.group(f"g{index}", "A group")

    with pytest.raises(CommandDefinitionError, match="already has 25 subcommands"):
        group.group("last", "One too many")


HALVES: object = Literal[0.5, 1.0]


def kitchen_sink() -> Command:
    async def sink(
        ctx: object,
        text: str,
        number: int,
        ratio: float,
        flag: bool,
        user: User,
        member: Member,
        role: Role,
        channel: PartialChannel,
        file: Attachment,
        who: User | Role,
        unit: Unit,
        size: Size,
        mode: Literal["sum", "list"],
        half: Any,
        maybe: int | None,
        sides: int = 6,
    ) -> None:
        pass

    sink.__annotations__["half"] = HALVES
    return Router().command(sink)


def test_bind_converts_every_option_type() -> None:
    given = [
        filled("text", 3, "hello"),
        filled("number", 4, 7),
        filled("ratio", 10, 2),
        filled("flag", 5, True),
        filled("user", 6, str(USER_ID)),
        filled("member", 6, str(USER_ID)),
        filled("role", 8, str(ROLE_ID)),
        filled("channel", 7, str(CHANNEL_ID)),
        filled("file", 11, str(ATTACHMENT_ID)),
        filled("who", 9, str(ROLE_ID)),
        filled("unit", 3, "imperial"),
        filled("size", 4, 2),
        filled("mode", 3, "list"),
        filled("half", 10, 1),
    ]

    arguments = kitchen_sink().bind(given, RESOLVED)

    assert arguments == {
        "text": "hello",
        "number": 7,
        "ratio": 2.0,
        "flag": True,
        "user": RESOLVED.users[USER_ID],
        "member": RESOLVED.member(USER_ID),
        "role": RESOLVED.roles[ROLE_ID],
        "channel": RESOLVED.channels[CHANNEL_ID],
        "file": RESOLVED.attachments[ATTACHMENT_ID],
        "who": RESOLVED.roles[ROLE_ID],
        "unit": Unit.IMPERIAL,
        "size": Size.LARGE,
        "mode": "list",
        "half": 1.0,
        "maybe": None,
        "sides": 6,
    }
    assert type(arguments["ratio"]) is float
    assert type(arguments["half"]) is float
    assert type(arguments["member"]) is Member


def test_mentionable_prefers_the_first_class_that_resolves() -> None:
    router = Router()

    @router.command
    async def poke(ctx: object, who: User | Role) -> None:
        pass

    data = {
        "users": {"5": payloads.user(id="5")},
        "roles": {"5": payloads.role(id="5")},
    }
    both = decode(Resolved, msgspec.json.encode(data))

    assert poke.bind([filled("who", 9, "5")], both) == {"who": both.users[5]}


@pytest.mark.parametrize(
    ("given", "resolved", "problem"),
    [
        ([], Resolved(), "required option 'name' is missing"),
        (
            [filled("name", 3, "x"), filled("other", 3, "y")],
            Resolved(),
            "no option 'other'",
        ),
        ([filled("name", 4, 1)], Resolved(), "no option 'name' of type 4"),
        ([filled("name", 3, 1)], Resolved(), "is STRING but got 1"),
        ([filled("name", 3, "x"), filled("count", 4, True)], Resolved(), "is INTEGER"),
        ([filled("name", 3, "x"), filled("unit", 3, "kelvin")], Resolved(), "choices"),
        ([filled("name", 3, "x"), filled("mode", 3, "avg")], Resolved(), "choices"),
        ([filled("name", 3, "x"), filled("member", 6, "9")], Resolved(), "no Member"),
        ([filled("name", 3, "x"), filled("member", 6, "abc")], Resolved(), "no Member"),
    ],
)
def test_bind_rejects_unexpected_input(
    given: list[InteractionDataOption], resolved: Resolved, problem: str
) -> None:
    router = Router()

    @router.command
    async def show(
        ctx: object,
        name: str,
        count: int | None,
        unit: Unit | None,
        mode: Literal["sum", "list"] | None,
        member: Member | None,
    ) -> None:
        pass

    with pytest.raises(OptionConversionError, match=problem):
        show.bind(given, resolved)


def test_autocomplete_functions_are_found_by_option_name() -> None:
    router = Router()

    @router.command
    async def tag(
        ctx: object, name: Annotated[str, Option(name="tag-name", autocomplete=suggest)]
    ) -> None:
        pass

    assert tag.autocompletes == {"tag-name": suggest}
    assert only_option(tag)["autocomplete"] is True


def test_autocomplete_choices_normalizes_values() -> None:
    result: list[str | int | float | Choice] = [
        "rules",
        3,
        1.5,
        Choice("Named", "value"),
    ]

    assert autocomplete_choices(result, suggest) == [
        {"name": "rules", "value": "rules"},
        {"name": "3", "value": 3},
        {"name": "1.5", "value": 1.5},
        {"name": "Named", "value": "value"},
    ]


def test_autocomplete_choices_fixes_what_discord_would_reject() -> None:
    long_name = Choice("n" * 150, "ok")
    long_value = "v" * 101
    result = [long_name, long_name, "", long_value, long_value, *range(30)]

    with pytest.warns(UserWarning, match="test_commands.suggest returned") as warned:
        choices = autocomplete_choices(result, suggest)

    assert choices[0] == {"name": "n" * 100, "value": "ok"}
    assert len(choices) == 25
    assert all(choice["name"] for choice in choices)
    assert all(choice["value"] != long_value for choice in choices)
    messages = sorted(str(warning.message) for warning in warned)
    assert len(messages) == 3
    assert all(
        message.startswith("test_commands.suggest returned") for message in messages
    )
    assert any("dropped" in message for message in messages)
    assert any("were cut" in message for message in messages)
    assert any("first 25" in message for message in messages)
