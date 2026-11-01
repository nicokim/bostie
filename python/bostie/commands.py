import inspect
import os
import typing
import unicodedata
import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from enum import Enum
from types import NoneType, UnionType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
    TypedDict,
    Union,
    Unpack,
    get_args,
    get_origin,
    overload,
)

from bostie.enums import CommandType, InteractionContextType, OptionType, Permissions
from bostie.errors import CommandDefinitionError, OptionConversionError

if TYPE_CHECKING:
    from typing import Annotated as Range

    from bostie.router import Router

__all__ = [
    "AutocompleteCallback",
    "Choice",
    "ChoiceValue",
    "Command",
    "CommandCallback",
    "CommandSettings",
    "Group",
    "GroupSettings",
    "Option",
    "Range",
    "Scope",
    "SubcommandSettings",
    "autocomplete_choices",
]

type ChoiceValue = str | int | float
type CommandCallback = Callable[..., Coroutine[Any, Any, None]]
type AutocompleteCallback = Callable[
    [Any, Any], Coroutine[Any, Any, Iterable[ChoiceValue | Choice]]
]
type Scope = Literal["global", "guild"]

LIBRARY_DIRECTORY = os.path.dirname(__file__)
MAX_OPTIONS = 25
MAX_CHOICES = 25
MAX_NAME = 32
MAX_TEXT = 100
MAX_LENGTH_BOUND = 6000
MAX_INTEGER = 2**53 - 1
MAX_NUMBER = 2**53
TEXT_BUDGET = 8000
DEFAULT_DESCRIPTION = "\N{HORIZONTAL ELLIPSIS}"
NAME_SYMBOLS = frozenset({"-", "_", "\N{MODIFIER LETTER APOSTROPHE}"})
NAME_SCRIPTS = (range(0x0900, 0x0980), range(0x0E00, 0x0E80))
CONTEXT_POSITIONAL = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
)
OPTION_KEYWORD = (
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.KEYWORD_ONLY,
)
SCALAR_TYPES: dict[object, OptionType] = {
    str: OptionType.STRING,
    int: OptionType.INTEGER,
    float: OptionType.NUMBER,
    bool: OptionType.BOOLEAN,
}
VALUE_TYPES: dict[OptionType, tuple[type, ...]] = {
    OptionType.STRING: (str,),
    OptionType.INTEGER: (int,),
    OptionType.NUMBER: (int, float),
    OptionType.BOOLEAN: (bool,),
}
CHOICE_TYPES = frozenset({OptionType.STRING, OptionType.INTEGER, OptionType.NUMBER})
NUMERIC_TYPES = frozenset({OptionType.INTEGER, OptionType.NUMBER})
FIELD_TYPES = {
    "choices": CHOICE_TYPES,
    "autocomplete": CHOICE_TYPES,
    "min_value": NUMERIC_TYPES,
    "max_value": NUMERIC_TYPES,
    "min_length": frozenset({OptionType.STRING}),
    "max_length": frozenset({OptionType.STRING}),
    "channel_types": frozenset({OptionType.CHANNEL}),
}
MENTIONABLE_TYPES = frozenset({OptionType.USER, OptionType.ROLE})


class GroupSettings(TypedDict, total=False):
    """Settings of a top-level command or group."""

    default_permissions: Permissions
    guild_only: bool
    nsfw: bool
    extra: Mapping[str, Any]


class SubcommandSettings(TypedDict, total=False):
    """Settings of a subcommand; without `auto_defer` it follows the bot's."""

    name: str
    description: str
    ephemeral: bool
    auto_defer: float | None
    extra: Mapping[str, Any]


class CommandSettings(SubcommandSettings, GroupSettings, total=False):
    """Settings of a top-level slash command."""


GROUP_SETTINGS = GroupSettings.__optional_keys__
SUBCOMMAND_SETTINGS = SubcommandSettings.__optional_keys__
COMMAND_SETTINGS = CommandSettings.__optional_keys__


@dataclass(frozen=True, slots=True)
class Choice:
    """A choice offered by an option or returned by an autocomplete function."""

    name: str
    value: ChoiceValue


@dataclass(frozen=True, slots=True, kw_only=True)
class Option:
    """Option settings, given as `Annotated[int, Option(...)]` metadata.

    Settings here override what the type hint implies.
    """

    name: str | None = None
    description: str | None = None
    autocomplete: AutocompleteCallback | None = None
    min: float | None = None
    max: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    channel_types: Iterable[int] | None = None
    choices: Iterable[Choice | ChoiceValue] | None = None
    extra: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _Bounds:
    low: Any
    high: Any


if not TYPE_CHECKING:

    class Range:
        """`Range[int, 1, 10]`: an int, float or str option with inclusive bounds."""

        def __class_getitem__(cls, parameters: tuple[object, Any, Any]) -> object:
            hint, low, high = parameters
            return Annotated[hint, _Bounds(low, high)]


@dataclass(frozen=True, slots=True)
class _Parameter:
    python_name: str
    payload: dict[str, Any]
    default: object
    classes: tuple[Any, ...]
    lookup: Callable[[Any], object] | None
    autocomplete: AutocompleteCallback | None

    @property
    def name(self) -> str:
        return str(self.payload["name"])

    @property
    def type(self) -> OptionType:
        return OptionType(self.payload["type"])

    @property
    def required(self) -> bool:
        return bool(self.payload["required"])

    def convert(self, value: Any, resolved: Any) -> object:
        if self.classes:
            return self._resolve(value, resolved)
        if type(value) not in VALUE_TYPES[self.type]:
            raise OptionConversionError(
                f"option {self.name!r} is {self.type.name} but got {value!r}"
            )
        if self.type is OptionType.NUMBER:
            value = float(value)
        if self.lookup is None:
            return value
        try:
            return self.lookup(value)
        except (KeyError, ValueError):
            raise OptionConversionError(
                f"option {self.name!r} got {value!r}, which is not one of its choices"
            ) from None

    def _resolve(self, value: object, resolved: Any) -> object:
        snowflake = int(value) if isinstance(value, str) and value.isdecimal() else None
        if snowflake is not None and resolved is not None:
            for cls in self.classes:
                found = cls.from_resolved(resolved, snowflake)
                if found is not None:
                    return found
        expected = " or ".join(cls.__name__ for cls in self.classes)
        raise OptionConversionError(
            f"option {self.name!r} got {value!r}, which resolves to no {expected}"
        )


class _Node(ABC):
    _option_type: ClassVar[OptionType]

    def __init__(
        self,
        name: str,
        description: str,
        *,
        router: "Router",
        parent: "Group | None",
        settings: GroupSettings,
    ) -> None:
        _check_name(name)
        _check_description(description)
        self.name = name
        self.description = description
        self.router = router
        self.parent = parent
        self.default_permissions = settings.get("default_permissions")
        self.guild_only = settings.get("guild_only", False)
        self.nsfw = settings.get("nsfw", False)
        self.extra = dict(settings.get("extra", {}))

    @property
    def qualified_name(self) -> str:
        """The full name as typed after the slash, such as "role color set"."""
        if self.parent is None:
            return self.name
        return f"{self.parent.qualified_name} {self.name}"

    def payload(self, scope: Scope) -> dict[str, Any]:
        """The JSON that registers this top-level command in the given scope."""
        permissions = self.default_permissions
        payload: dict[str, Any] = {
            "type": CommandType.CHAT_INPUT,
            "name": self.name,
            "description": self.description,
            "options": self._options(),
            "default_member_permissions": None
            if permissions is None
            else str(permissions.value),
            "nsfw": self.nsfw,
        }
        if scope == "global":
            payload["contexts"] = (
                [InteractionContextType.GUILD]
                if self.guild_only
                else [InteractionContextType.GUILD, InteractionContextType.BOT_DM]
            )
        return payload | self.extra

    def _as_option(self) -> dict[str, Any]:
        return {
            "type": self._option_type,
            "name": self.name,
            "description": self.description,
            "options": self._options(),
        } | self.extra

    @abstractmethod
    def _options(self) -> list[dict[str, Any]]: ...


class Command(_Node):
    """A slash command or subcommand built from an async function."""

    _option_type = OptionType.SUB_COMMAND

    def __init__(
        self,
        callback: CommandCallback,
        *,
        router: "Router",
        parent: "Group | None" = None,
        **settings: Unpack[CommandSettings],
    ) -> None:
        _check_settings(
            settings, COMMAND_SETTINGS if parent is None else SUBCOMMAND_SETTINGS
        )
        try:
            super().__init__(
                settings.get("name", callback.__name__),
                _command_description(callback, settings.get("description")),
                router=router,
                parent=parent,
                settings=settings,
            )
            self._parameters = _parameters(callback)
        except CommandDefinitionError as error:
            raise CommandDefinitionError(f"{_python_name(callback)}: {error}") from None
        self.callback = callback
        self.ephemeral = settings.get("ephemeral", False)
        self._settings = settings
        self.autocompletes: dict[str, AutocompleteCallback] = {
            parameter.name: parameter.autocomplete
            for parameter in self._parameters
            if parameter.autocomplete is not None
        }
        self._by_option_name = {
            parameter.name: parameter for parameter in self._parameters
        }
        if parent is None:
            _warn_if_over_budget(self)

    @property
    def auto_defer(self) -> float | None:
        """Seconds before an unanswered call is deferred; the bot's unless set here."""
        if "auto_defer" in self._settings:
            return self._settings["auto_defer"]
        return self.router.bot.auto_defer

    def bind(self, options: Sequence[Any], resolved: Any) -> dict[str, Any]:
        """Turn the leaf options of an invocation into keyword arguments."""
        arguments: dict[str, Any] = {}
        for option in options:
            parameter = self._by_option_name.get(option.name)
            if parameter is None or option.type != parameter.type:
                raise OptionConversionError(
                    f"/{self.qualified_name} has no option {option.name!r} "
                    f"of type {option.type}"
                )
            arguments[parameter.python_name] = parameter.convert(option.value, resolved)
        for parameter in self._parameters:
            if parameter.python_name in arguments:
                continue
            if parameter.required:
                raise OptionConversionError(
                    f"required option {parameter.name!r} is missing"
                )
            arguments[parameter.python_name] = parameter.default
        return arguments

    def _options(self) -> list[dict[str, Any]]:
        return [parameter.payload for parameter in self._parameters]


class Group(_Node):
    """A slash command made of subcommands, or a subcommand group inside one."""

    _option_type = OptionType.SUB_COMMAND_GROUP

    def __init__(
        self,
        name: str,
        description: str,
        *,
        router: "Router",
        parent: "Group | None" = None,
        **settings: Unpack[GroupSettings],
    ) -> None:
        _check_settings(settings, GROUP_SETTINGS)
        try:
            super().__init__(
                name, description, router=router, parent=parent, settings=settings
            )
        except CommandDefinitionError as error:
            raise CommandDefinitionError(f"group {name!r}: {error}") from None
        self.commands: dict[str, Command | Group] = {}

    def group(self, name: str, description: str) -> "Group":
        """Add a subcommand group; groups nest one level deep at most."""
        if self.parent is not None:
            raise CommandDefinitionError(
                f"/{self.qualified_name} {name}: groups nest one level deep at most"
            )
        return self._add(Group(name, description, router=self.router, parent=self))

    @overload
    def command(self, callback: CommandCallback, /) -> Command: ...

    @overload
    def command(
        self, callback: None = None, /, **settings: Unpack[SubcommandSettings]
    ) -> Callable[[CommandCallback], Command]: ...

    def command(
        self,
        callback: CommandCallback | None = None,
        /,
        **settings: Unpack[SubcommandSettings],
    ) -> Command | Callable[[CommandCallback], Command]:
        """Add a subcommand, as `@group.command` or `@group.command(name=...)`."""

        def register(callback: CommandCallback) -> Command:
            return self._add(
                Command(callback, router=self.router, parent=self, **settings)
            )

        return register if callback is None else register(callback)

    def _add[N: (Command, Group)](self, node: N) -> N:
        if node.name in self.commands:
            raise CommandDefinitionError(
                f"/{self.qualified_name} already has a subcommand named {node.name!r}"
            )
        if len(self.commands) == MAX_OPTIONS:
            raise CommandDefinitionError(
                f"/{self.qualified_name} already has 25 subcommands"
            )
        self.commands[node.name] = node
        _warn_if_over_budget(self)
        return node

    def _options(self) -> list[dict[str, Any]]:
        return [node._as_option() for node in self.commands.values()]


def autocomplete_choices(
    result: Iterable[ChoiceValue | Choice], function: Callable[..., object]
) -> list[dict[str, Any]]:
    """Turn what an autocomplete function returned into at most 25 valid choices."""
    choices: list[dict[str, Any]] = []
    truncated = dropped = False
    for item in result:
        choice = item if isinstance(item, Choice) else Choice(str(item), item)
        if not choice.name or (
            isinstance(choice.value, str) and len(choice.value) > MAX_TEXT
        ):
            dropped = True
            continue
        truncated = truncated or len(choice.name) > MAX_TEXT
        choices.append({"name": choice.name[:MAX_TEXT], "value": choice.value})
    where = _python_name(function)
    if dropped:
        _warn(
            f"{where} returned empty names or string values over 100 characters; "
            "those choices were dropped"
        )
    if truncated:
        _warn(f"{where} returned choice names over 100 characters; they were cut")
    if len(choices) > MAX_CHOICES:
        _warn(f"{where} returned more than 25 choices; only the first 25 were sent")
    return choices[:MAX_CHOICES]


def _parameters(callback: CommandCallback) -> list[_Parameter]:
    signature = list(inspect.signature(callback).parameters.values())
    if not signature or signature[0].kind not in CONTEXT_POSITIONAL:
        raise CommandDefinitionError(
            "the first parameter must take the context positionally"
        )
    hints = typing.get_type_hints(callback, include_extras=True)
    parameters = [_parameter(parameter, hints) for parameter in signature[1:]]
    if len(parameters) > MAX_OPTIONS:
        raise CommandDefinitionError(f"{len(parameters)} options; Discord allows 25")
    seen: set[str] = set()
    for parameter in parameters:
        if parameter.name in seen:
            raise CommandDefinitionError(f"two options are named {parameter.name!r}")
        seen.add(parameter.name)
    return sorted(parameters, key=lambda parameter: not parameter.required)


def _parameter(parameter: inspect.Parameter, hints: Mapping[str, object]) -> _Parameter:
    try:
        if parameter.kind not in OPTION_KEYWORD:
            raise CommandDefinitionError(
                "options cannot be *args, **kwargs or positional-only"
            )
        if parameter.name not in hints:
            raise CommandDefinitionError("options need a type hint")
        return _infer(parameter, hints[parameter.name])
    except CommandDefinitionError as error:
        raise CommandDefinitionError(f"parameter {parameter.name!r}: {error}") from None


def _infer(parameter: inspect.Parameter, hint: object) -> _Parameter:
    members, optional, metadata = _unwrap(hint)
    option_type, choices, classes, lookup = _kind(members)
    settings = _merge(
        [
            *(
                _bounds(option_type, item)
                for item in metadata
                if isinstance(item, _Bounds)
            ),
            *(item for item in metadata if isinstance(item, Option)),
        ]
    )
    if settings.choices is not None:
        if choices:
            raise CommandDefinitionError(
                "the hint already sets the choices; remove Option(choices=)"
            )
        choices = [
            choice if isinstance(choice, Choice) else Choice(str(choice), choice)
            for choice in settings.choices
        ]
    descriptions = [item for item in metadata if isinstance(item, str)]
    if descriptions:
        description = descriptions[-1]
    elif settings.description is not None:
        description = settings.description
    else:
        description = DEFAULT_DESCRIPTION
    payload: dict[str, Any] = {
        "type": option_type,
        "name": parameter.name if settings.name is None else settings.name,
        "description": description,
        "required": parameter.default is inspect.Parameter.empty and not optional,
    }
    optional_fields = {
        "choices": [{"name": choice.name, "value": choice.value} for choice in choices]
        or None,
        "autocomplete": True if settings.autocomplete is not None else None,
        "min_value": settings.min,
        "max_value": settings.max,
        "min_length": settings.min_length,
        "max_length": settings.max_length,
        "channel_types": None
        if settings.channel_types is None
        else list(settings.channel_types),
    }
    payload |= {
        key: value for key, value in optional_fields.items() if value is not None
    }
    payload |= settings.extra or {}
    _check_option(payload)
    default = (
        None if parameter.default is inspect.Parameter.empty else parameter.default
    )
    return _Parameter(
        parameter.name, payload, default, classes, lookup, settings.autocomplete
    )


def _unwrap(hint: object) -> tuple[tuple[object, ...], bool, list[object]]:
    members: tuple[object, ...] = (hint,)
    optional = False
    metadata: list[object] = []
    while len(members) == 1:
        hint = members[0]
        if get_origin(hint) is Annotated:
            hint, *extras = get_args(hint)
            metadata[:0] = extras
            members = (hint,)
        elif get_origin(hint) in (Union, UnionType):
            arguments = get_args(hint)
            optional = optional or NoneType in arguments
            members = tuple(
                argument for argument in arguments if argument is not NoneType
            )
        else:
            break
    return members, optional, metadata


def _kind(
    members: tuple[object, ...],
) -> tuple[OptionType, list[Choice], tuple[Any, ...], Callable[[Any], object] | None]:
    if len(members) == 2 and {_model_type(member) for member in members} == (
        MENTIONABLE_TYPES
    ):
        return OptionType.MENTIONABLE, [], members, None
    hint = members[0] if len(members) == 1 else None
    if hint in SCALAR_TYPES:
        return SCALAR_TYPES[hint], [], (), None
    if isinstance(hint, type) and issubclass(hint, Enum):
        choices = [Choice(member.name, member.value) for member in hint]
        return _choice_type(choices), choices, (), hint
    if get_origin(hint) is Literal:
        values = get_args(hint)
        choices = [Choice(str(value), value) for value in values]
        lookup = {value: value for value in values}.__getitem__
        return _choice_type(choices), choices, (), lookup
    model_type = _model_type(hint)
    if model_type is not None:
        return model_type, [], (hint,), None
    raise CommandDefinitionError(
        "unsupported type hint " + " | ".join(map(inspect.formatannotation, members))
    )


def _model_type(hint: object) -> OptionType | None:
    option_type = (
        getattr(hint, "__option_type__", None) if isinstance(hint, type) else None
    )
    return None if option_type is None else OptionType(option_type)


def _choice_type(choices: Sequence[Choice]) -> OptionType:
    kinds = {SCALAR_TYPES.get(type(choice.value)) for choice in choices}
    kind = kinds.pop() if len(kinds) == 1 else None
    if kind is None or kind not in CHOICE_TYPES:
        raise CommandDefinitionError(
            "Literal and Enum values must be all str, all int or all float"
        )
    return kind


def _bounds(option_type: OptionType, bounds: _Bounds) -> Option:
    if option_type is OptionType.STRING:
        return Option(min_length=bounds.low, max_length=bounds.high)
    return Option(min=bounds.low, max=bounds.high)


def _merge(options: Iterable[Option]) -> Option:
    merged = Option()
    for option in options:
        merged = replace(
            merged,
            **{
                field.name: getattr(option, field.name)
                for field in fields(option)
                if getattr(option, field.name) is not None
            },
        )
    return merged


def _check_option(payload: Mapping[str, Any]) -> None:
    option_type = OptionType(payload["type"])
    _check_name(payload["name"])
    _check_description(payload["description"])
    for field, allowed in FIELD_TYPES.items():
        if field in payload and option_type not in allowed:
            raise CommandDefinitionError(
                f"{field} does not apply to {option_type.name} options"
            )
    if "choices" in payload and "autocomplete" in payload:
        raise CommandDefinitionError("autocomplete cannot be combined with choices")
    _check_choices(option_type, payload.get("choices", []))
    _check_bounds(option_type, payload)


def _check_choices(
    option_type: OptionType, choices: Sequence[Mapping[str, Any]]
) -> None:
    if len(choices) > MAX_CHOICES:
        raise CommandDefinitionError(f"{len(choices)} choices; Discord allows 25")
    for choice in choices:
        name, value = choice["name"], choice["value"]
        if not 1 <= len(name) <= MAX_TEXT:
            raise CommandDefinitionError(
                f"choice name {name!r} must be 1-100 characters long"
            )
        if type(value) not in VALUE_TYPES[option_type]:
            raise CommandDefinitionError(
                f"choice {name!r} has a {type(value).__name__} value "
                f"for a {option_type.name} option"
            )
        if isinstance(value, str) and len(value) > MAX_TEXT:
            raise CommandDefinitionError(
                f"choice value {value!r} is longer than 100 characters"
            )


def _check_bounds(option_type: OptionType, payload: Mapping[str, Any]) -> None:
    limit = MAX_NUMBER if option_type is OptionType.NUMBER else MAX_INTEGER
    if any(abs(payload.get(key) or 0) > limit for key in ("min_value", "max_value")):
        raise CommandDefinitionError(f"min and max must be within -{limit}..{limit}")
    if not 0 <= payload.get("min_length", 0) <= MAX_LENGTH_BOUND:
        raise CommandDefinitionError("min_length must be 0-6000")
    if not 1 <= payload.get("max_length", 1) <= MAX_LENGTH_BOUND:
        raise CommandDefinitionError("max_length must be 1-6000")
    for low, high in (("min_value", "max_value"), ("min_length", "max_length")):
        if low in payload and high in payload and payload[low] > payload[high]:
            raise CommandDefinitionError(f"{low} is greater than {high}")


def _check_name(name: str) -> None:
    if not (1 <= len(name) <= MAX_NAME and all(map(_is_name_character, name))):
        raise CommandDefinitionError(
            f"{name!r} is not a valid name: use 1-32 lowercase letters, "
            "digits, '-' or '_'"
        )


def _is_name_character(character: str) -> bool:
    if character in NAME_SYMBOLS:
        return True
    if character != character.lower():
        return False
    return unicodedata.category(character)[0] in "LN" or any(
        ord(character) in script for script in NAME_SCRIPTS
    )


def _check_description(description: str) -> None:
    if not 1 <= len(description) <= MAX_TEXT:
        raise CommandDefinitionError(
            f"description {description!r} must be 1-100 characters long"
        )


def _check_settings(settings: Mapping[str, object], allowed: frozenset[str]) -> None:
    unknown = settings.keys() - allowed
    if unknown:
        raise TypeError(f"unexpected settings: {', '.join(sorted(unknown))}")


def _command_description(callback: CommandCallback, description: str | None) -> str:
    if description is not None:
        return description
    docstring = inspect.getdoc(callback)
    return docstring.splitlines()[0] if docstring else DEFAULT_DESCRIPTION


def _warn_if_over_budget(node: _Node) -> None:
    while node.parent is not None:
        node = node.parent
    size = _text_size(node.payload("guild"))
    if size > TEXT_BUDGET:
        _warn(
            f"/{node.name} uses {size} characters for names, descriptions and "
            "choice values; Discord allows 8000 per command"
        )


def _text_size(payload: Mapping[str, Any]) -> int:
    return (
        len(payload["name"])
        + len(payload["description"])
        + sum(
            len(choice["name"]) + len(str(choice["value"]))
            for choice in payload.get("choices", ())
        )
        + sum(map(_text_size, payload.get("options", ())))
    )


def _python_name(function: Callable[..., object]) -> str:
    return f"{function.__module__}.{function.__qualname__}"


def _warn(message: str) -> None:
    warnings.warn(message, stacklevel=2, skip_file_prefixes=(LIBRARY_DIRECTORY,))
