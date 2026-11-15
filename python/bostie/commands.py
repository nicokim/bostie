import contextlib
import inspect
import re
import typing
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import (
    Awaitable,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field, fields, replace
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
    cast,
    get_args,
    get_origin,
    overload,
    override,
)

from bostie._util import qualified_name, warn_user
from bostie.enums import (
    ApplicationIntegrationType,
    ChannelType,
    CommandType,
    EntryPointCommandHandlerType,
    InteractionContextType,
    OptionType,
    Permissions,
)
from bostie.errors import CommandDefinitionError, OptionConversionError
from bostie.hooks import Answering
from bostie.models import (
    Attachment,
    Channel,
    CommandInteraction,
    InteractionDataOption,
    Member,
    Message,
    PartialChannel,
    PartialMessage,
    PartialUser,
    Resolved,
    ResolvedMember,
    Role,
    User,
    WebhookMessage,
)
from bostie.models.base import check_keys
from bostie.models.message import in_guild

if TYPE_CHECKING:
    from typing import Annotated as Range

    from bostie.context import AutocompleteContext
    from bostie.router import Router

__all__ = [
    "AutocompleteCallback",
    "Check",
    "Choice",
    "ChoiceValue",
    "Command",
    "CommandCallback",
    "CommandDefaults",
    "CommandSettings",
    "ContextMenuCommand",
    "ContextMenuSettings",
    "Convert",
    "EntryPointCommand",
    "EntryPointSettings",
    "Group",
    "GroupSettings",
    "Option",
    "Range",
    "Scope",
    "SubcommandSettings",
    "SubgroupSettings",
    "autocomplete_choices",
]

type ChoiceValue = str | int | float
type Check = Callable[[Any], bool | Awaitable[bool]]
type CommandCallback = Callable[..., Coroutine[Any, Any, None]]
type AutocompleteCallback = Callable[
    ["AutocompleteContext", Any], Coroutine[Any, Any, Iterable[ChoiceValue | Choice]]
]
type OptionModel = type[User | ResolvedMember | Role | PartialChannel | Attachment]
type TargetModel = type[User | ResolvedMember | Message | WebhookMessage]
type TargetType = Literal[CommandType.USER, CommandType.MESSAGE]
type Scope = Literal["global", "guild"]

MAX_OPTIONS = 25
MAX_CHOICES = 25
MAX_NAME = 32
MAX_TEXT = 100
MAX_LENGTH_BOUND = 6000
MAX_INTEGER = 2**53 - 1
MAX_NUMBER = 2**53
TEXT_BUDGET = 8000
MAX_FILE_TYPES = 10
CONVERTER_ARITY = 2
DEFAULT_DESCRIPTION = "\N{HORIZONTAL ELLIPSIS}"
GOOGLE_ENTRY = re.compile(r"(?P<name>\w+)\s*(?:\([^)]*\))?\s*:\s*(?P<text>.*)")
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
    "file_types": frozenset({OptionType.ATTACHMENT}),
}
MENTIONABLE_TYPES = frozenset({OptionType.USER, OptionType.ROLE})
OPTION_MODELS: dict[OptionModel, OptionType] = {
    User: OptionType.USER,
    ResolvedMember: OptionType.USER,
    Role: OptionType.ROLE,
    PartialChannel: OptionType.CHANNEL,
    Attachment: OptionType.ATTACHMENT,
}
MODEL_ADVICE: dict[type, str] = {
    Channel: "a channel option is a PartialChannel",
    Member: "a member option is a ResolvedMember",
    PartialUser: "a user option is a User or a ResolvedMember",
}
TARGET_MODELS: dict[CommandType, tuple[TargetModel, ...]] = {
    CommandType.USER: (User, ResolvedMember),
    CommandType.MESSAGE: (Message, WebhookMessage),
}


class CommandDefaults(TypedDict, total=False):
    """Where global commands can be used, unless a command says otherwise."""

    contexts: Iterable[InteractionContextType]
    integration_types: Iterable[ApplicationIntegrationType]


class _TopLevelSettings(CommandDefaults, total=False):
    default_permissions: Permissions
    nsfw: bool
    guilds: Iterable[int]
    name_localizations: Mapping[str, str]
    checks: Iterable[Check]
    extra: Mapping[str, Any]


class GroupSettings(_TopLevelSettings, total=False):
    """Settings of a top-level command or group.

    `guilds` registers it in those guilds only instead of globally, and
    `contexts` and `integration_types` apply to global commands only.
    Localizations map a locale such as "fr" to the text shown in it.
    """

    description_localizations: Mapping[str, str]


class SubgroupSettings(TypedDict, total=False):
    """Settings of a subcommand group inside a command.

    `checks` run before the handler, after those of the routers and parents.
    """

    name_localizations: Mapping[str, str]
    description_localizations: Mapping[str, str]
    checks: Iterable[Check]
    extra: Mapping[str, Any]


class SubcommandSettings(SubgroupSettings, total=False):
    """Settings of a subcommand; without `auto_defer` it follows the bot's."""

    name: str
    description: str
    ephemeral: bool
    auto_defer: float | None


class CommandSettings(SubcommandSettings, GroupSettings, total=False):
    """Settings of a top-level slash command."""


class ContextMenuSettings(_TopLevelSettings, total=False):
    """Settings of a user or message command; `auto_defer` defaults to the bot's."""

    ephemeral: bool
    auto_defer: float | None


class EntryPointSettings(CommandDefaults, total=False):
    """Settings of the app's entry point command, which is always global."""

    description: str
    default_permissions: Permissions
    nsfw: bool
    name_localizations: Mapping[str, str]
    description_localizations: Mapping[str, str]
    ephemeral: bool
    auto_defer: float | None
    checks: Iterable[Check]
    extra: Mapping[str, Any]


GROUP_SETTINGS = GroupSettings.__optional_keys__
ENTRY_POINT_SETTINGS = EntryPointSettings.__optional_keys__
SUBGROUP_SETTINGS = SubgroupSettings.__optional_keys__
SUBCOMMAND_SETTINGS = SubcommandSettings.__optional_keys__
COMMAND_SETTINGS = CommandSettings.__optional_keys__
CONTEXT_MENU_SETTINGS = ContextMenuSettings.__optional_keys__


@dataclass(frozen=True, slots=True)
class Choice:
    """A choice offered by an option or returned by an autocomplete function."""

    name: str
    value: ChoiceValue
    name_localizations: Mapping[str, str] | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class Option:
    """Option settings, given as `Annotated[int, Option(...)]` metadata.

    Settings here override what the type hint implies.
    """

    name: str | None = None
    description: str | None = None
    name_localizations: Mapping[str, str] | None = None
    description_localizations: Mapping[str, str] | None = None
    autocomplete: AutocompleteCallback | None = None
    min: float | None = None
    max: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    channel_types: Iterable[ChannelType] | None = None
    file_types: Iterable[str] | None = None
    choices: Iterable[Choice | ChoiceValue] | None = None
    extra: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Convert:
    """`Annotated[T, Convert(f)]`: `f(ctx, value)` turns the value into a T.

    `f` may be async, and the option's type comes from its value's type hint.
    """

    function: Callable[[Any, Any], Any]


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
    models: tuple[OptionModel, ...]
    lookup: Callable[[Any], object] | None
    autocomplete: AutocompleteCallback | None
    converter: Callable[[Any, Any], Any] | None = None

    @property
    def name(self) -> str:
        return str(self.payload["name"])

    @property
    def type(self) -> OptionType:
        return OptionType(self.payload["type"])

    @property
    def required(self) -> bool:
        return bool(self.payload["required"])

    def convert(self, value: Any, resolved: Resolved) -> object:
        if self.models:
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

    def _resolve(self, value: object, resolved: Resolved) -> object:
        snowflake = int(value) if isinstance(value, str) and value.isdecimal() else None
        if snowflake is not None:
            for model in self.models:
                found = model.from_resolved(resolved, snowflake)
                if found is not None:
                    return found
        expected = " or ".join(model.__name__ for model in self.models)
        raise OptionConversionError(
            f"option {self.name!r} got {value!r}, which resolves to no {expected}"
        )


class _Registered(Answering):
    def __init__(
        self,
        type: CommandType,
        name: str,
        *,
        router: "Router",
        settings: _TopLevelSettings | SubgroupSettings | EntryPointSettings,
    ) -> None:
        super().__init__(router, settings)
        self.type = type
        self.name = name
        self.checks = tuple(settings.get("checks", ()))
        self.name_localizations = dict(settings.get("name_localizations", {}))
        for localized in self.name_localizations.values():
            _check_name(localized, spaced=type is not CommandType.CHAT_INPUT)
        self.guilds = tuple(cast("_TopLevelSettings", settings).get("guilds", ()))
        self.extra = dict(settings.get("extra", {}))

    def payload(
        self, scope: Scope, defaults: CommandDefaults | None = None
    ) -> dict[str, Any]:
        """The JSON that registers this top-level command in the given scope."""
        permissions = self._inherited("default_permissions", None)
        payload: dict[str, Any] = {
            "type": self.type,
            "name": self.name,
            **_localized("name_localizations", self.name_localizations),
            **self._fields(),
            "default_member_permissions": None
            if permissions is None
            else str(permissions.value),
            "nsfw": self._inherited("nsfw", False),
        }
        if scope == "global":
            for key in ("contexts", "integration_types"):
                found = self._inherited(key, None)
                if found is None and defaults is not None:
                    found = cast("Mapping[str, Any]", defaults).get(key)
                if found is not None:
                    payload[key] = list(found)
        return payload | self.extra

    def _fields(self) -> dict[str, Any]:
        return {}


class _Node(_Registered, ABC):
    _option_type: ClassVar[OptionType]

    def __init__(
        self,
        name: str,
        description: str,
        *,
        router: "Router",
        parent: "Group | None",
        settings: GroupSettings | SubgroupSettings,
    ) -> None:
        _check_name(name)
        _check_description(description)
        super().__init__(CommandType.CHAT_INPUT, name, router=router, settings=settings)
        self.description = description
        self.description_localizations = dict(
            settings.get("description_localizations", {})
        )
        for localized in self.description_localizations.values():
            _check_description(localized)
        self.parent = parent

    @property
    def qualified_name(self) -> str:
        """The full name as typed after the slash, such as "role color set"."""
        if self.parent is None:
            return self.name
        return f"{self.parent.qualified_name} {self.name}"

    @override
    def _fields(self) -> dict[str, Any]:
        return {
            "description": self.description,
            **_localized("description_localizations", self.description_localizations),
            "options": self._options(),
        }

    def option_payload(self) -> dict[str, Any]:
        """The JSON of this subcommand or group as an option of its parent."""
        return {
            "type": self._option_type,
            "name": self.name,
            **_localized("name_localizations", self.name_localizations),
            **self._fields(),
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
        check_keys(
            settings,
            COMMAND_SETTINGS if parent is None else SUBCOMMAND_SETTINGS,
            "settings",
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
            raise CommandDefinitionError(
                f"{qualified_name(callback)}: {error}"
            ) from None
        self.callback = callback
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

    def bind(
        self, options: Sequence[InteractionDataOption], resolved: Resolved
    ) -> dict[str, Any]:
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

    def filled(
        self, options: Sequence[InteractionDataOption], resolved: Resolved
    ) -> dict[str, Any]:
        """The options given so far by name, converted as far as they convert.

        The focused option, and a partial value that does not convert, stay as sent.
        """
        filled: dict[str, Any] = {}
        for option in options:
            filled[option.name] = option.value
            parameter = self._by_option_name.get(option.name)
            if parameter is None or option.focused or option.type != parameter.type:
                continue
            with contextlib.suppress(OptionConversionError):
                filled[option.name] = parameter.convert(option.value, resolved)
        return filled

    async def arguments(
        self,
        ctx: object,
        options: Sequence[InteractionDataOption],
        resolved: Resolved,
    ) -> dict[str, Any]:
        """The keyword arguments of an invocation, with `Convert` functions applied."""
        arguments = self.bind(options, resolved)
        given = {option.name for option in options}
        for parameter in self._parameters:
            if parameter.converter is None or parameter.name not in given:
                continue
            value = parameter.converter(ctx, arguments[parameter.python_name])
            if inspect.isawaitable(value):
                value = await value
            arguments[parameter.python_name] = value
        return arguments

    @override
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
        check_keys(
            settings,
            GROUP_SETTINGS if parent is None else SUBGROUP_SETTINGS,
            "settings",
        )
        try:
            super().__init__(
                name, description, router=router, parent=parent, settings=settings
            )
        except CommandDefinitionError as error:
            raise CommandDefinitionError(f"group {name!r}: {error}") from None
        self.commands: dict[str, Command | Group] = {}

    def group(
        self, name: str, description: str, **settings: Unpack[SubgroupSettings]
    ) -> "Group":
        """Add a subcommand group; groups nest one level deep at most."""
        if self.parent is not None:
            raise CommandDefinitionError(
                f"/{self.qualified_name} {name}: groups nest one level deep at most"
            )
        return self._add(
            Group(name, description, router=self.router, parent=self, **settings)
        )

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

    @override
    def _options(self) -> list[dict[str, Any]]:
        return [node.option_payload() for node in self.commands.values()]


class ContextMenuCommand(_Registered):
    """A user or message command, run from the context menu of its target."""

    def __init__(
        self,
        type: TargetType,
        name: str,
        callback: CommandCallback,
        *,
        router: "Router",
        **settings: Unpack[ContextMenuSettings],
    ) -> None:
        check_keys(settings, CONTEXT_MENU_SETTINGS, "settings")
        try:
            _check_name(name, spaced=True)
            self._target, self._models = _target(callback, TARGET_MODELS[type])
        except CommandDefinitionError as error:
            raise CommandDefinitionError(
                f"{qualified_name(callback)}: {error}"
            ) from None
        super().__init__(type, name, router=router, settings=settings)
        self.callback = callback

    def bind(self, interaction: CommandInteraction) -> dict[str, Any]:
        """Resolve the user or message the command was run on into its argument."""
        data = interaction.data
        for model in self._models:
            found = (
                None
                if data.target_id is None
                else model.from_resolved(data.resolved, data.target_id)
            )
            if isinstance(found, PartialMessage):
                found = in_guild(found, interaction.guild_id)
            if found is not None:
                return {self._target: found}
        expected = " or ".join(model.__name__ for model in self._models)
        raise OptionConversionError(
            f"the target {data.target_id} of {self.name!r} resolves to no {expected}"
        )


class EntryPointCommand(_Registered):
    """The app's entry point command, which opens its Activity from the App Launcher."""

    def __init__(
        self,
        name: str,
        callback: CommandCallback,
        *,
        router: "Router",
        **settings: Unpack[EntryPointSettings],
    ) -> None:
        check_keys(settings, ENTRY_POINT_SETTINGS, "settings")
        description = _command_description(callback, settings.get("description"))
        try:
            _check_name(name, spaced=True)
            _check_description(description)
            _check_takes_only_the_context(callback)
        except CommandDefinitionError as error:
            raise CommandDefinitionError(
                f"{qualified_name(callback)}: {error}"
            ) from None
        super().__init__(
            CommandType.PRIMARY_ENTRY_POINT, name, router=router, settings=settings
        )
        self.description = description
        self.description_localizations = dict(
            settings.get("description_localizations", {})
        )
        self.callback = callback

    @override
    def _fields(self) -> dict[str, Any]:
        return {
            "description": self.description,
            **_localized("description_localizations", self.description_localizations),
            "handler": EntryPointCommandHandlerType.APP_HANDLER,
        }


def autocomplete_choices(
    result: Iterable[ChoiceValue | Choice],
    function: Callable[..., object],
    option_type: OptionType,
) -> list[dict[str, Any]]:
    """Turn what an autocomplete function returned into at most 25 valid choices."""
    choices: list[dict[str, Any]] = []
    truncated = dropped = False
    for item in result:
        choice = item if isinstance(item, Choice) else Choice(str(item), item)
        if (
            not choice.name
            or type(choice.value) not in VALUE_TYPES[option_type]
            or (isinstance(choice.value, str) and len(choice.value) > MAX_TEXT)
        ):
            dropped = True
            continue
        truncated = truncated or len(choice.name) > MAX_TEXT
        choices.append(_choice(replace(choice, name=choice.name[:MAX_TEXT])))
    where = qualified_name(function)
    if dropped:
        warn_user(
            f"{where} returned empty names, values that are not "
            f"{option_type.name} or strings over 100 characters; "
            "those choices were dropped"
        )
    if truncated:
        warn_user(f"{where} returned choice names over 100 characters; they were cut")
    if len(choices) > MAX_CHOICES:
        warn_user(f"{where} returned more than 25 choices; only the first 25 were sent")
    return choices[:MAX_CHOICES]


def _parameters(callback: CommandCallback) -> list[_Parameter]:
    signature = list(inspect.signature(callback).parameters.values())
    if not signature or signature[0].kind not in CONTEXT_POSITIONAL:
        raise CommandDefinitionError(
            "the first parameter must take the context positionally"
        )
    hints = typing.get_type_hints(callback, include_extras=True)
    documented = _documented(callback)
    parameters = [
        _parameter(parameter, hints, documented.get(parameter.name))
        for parameter in signature[1:]
    ]
    if len(parameters) > MAX_OPTIONS:
        raise CommandDefinitionError(f"{len(parameters)} options; Discord allows 25")
    seen: set[str] = set()
    for parameter in parameters:
        if parameter.name in seen:
            raise CommandDefinitionError(f"two options are named {parameter.name!r}")
        seen.add(parameter.name)
    return sorted(parameters, key=lambda parameter: not parameter.required)


def _target(
    callback: CommandCallback, allowed: tuple[TargetModel, ...]
) -> tuple[str, tuple[TargetModel, ...]]:
    match list(inspect.signature(callback).parameters.values()):
        case [context, target] if (
            context.kind in CONTEXT_POSITIONAL and target.kind in OPTION_KEYWORD
        ):
            hints = typing.get_type_hints(callback, include_extras=True)
            members, optional, _ = _unwrap(hints.get(target.name))
            models = tuple(
                model for member in members for model in allowed if member is model
            )
            if not optional and len(models) == len(members):
                return target.name, models
            expected = " or a ".join(model.__name__ for model in allowed)
            raise CommandDefinitionError(
                f"parameter {target.name!r} must be a {expected}"
            )
        case _:
            raise CommandDefinitionError(
                "the function must take the context positionally, then the target"
            )


def _check_takes_only_the_context(callback: CommandCallback) -> None:
    match list(inspect.signature(callback).parameters.values()):
        case [context] if context.kind in CONTEXT_POSITIONAL:
            return
        case _:
            raise CommandDefinitionError("the function must take only the context")


def _parameter(
    parameter: inspect.Parameter, hints: Mapping[str, object], documented: str | None
) -> _Parameter:
    try:
        return _infer(parameter, _hint(parameter, hints), documented)
    except CommandDefinitionError as error:
        raise CommandDefinitionError(f"parameter {parameter.name!r}: {error}") from None


def _hint(parameter: inspect.Parameter, hints: Mapping[str, object]) -> object:
    if parameter.kind not in OPTION_KEYWORD:
        raise CommandDefinitionError(
            "options cannot be *args, **kwargs or positional-only"
        )
    if parameter.name not in hints:
        raise CommandDefinitionError("options need a type hint")
    return hints[parameter.name]


def _infer(
    parameter: inspect.Parameter, hint: object, documented: str | None
) -> _Parameter:
    members, optional, metadata = _unwrap(hint)
    converter = next(
        (item.function for item in reversed(metadata) if isinstance(item, Convert)),
        None,
    )
    if converter is not None:
        members, _, source = _unwrap(_value_hint(converter))
        metadata = [*source, *metadata]
    option_type, choices, models, lookup = _kind(members)
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
        given = [
            choice if isinstance(choice, Choice) else Choice(str(choice), choice)
            for choice in settings.choices
        ]
        if choices and [choice.value for choice in given] != [
            choice.value for choice in choices
        ]:
            raise CommandDefinitionError(
                "Option(choices=) must list the values the hint gives, in order"
            )
        choices = given
    descriptions = [item for item in metadata if isinstance(item, str)]
    if descriptions:
        description = descriptions[-1]
    elif settings.description is not None:
        description = settings.description
    else:
        description = documented or DEFAULT_DESCRIPTION
    payload: dict[str, Any] = {
        "type": option_type,
        "name": parameter.name if settings.name is None else settings.name,
        **_localized("name_localizations", settings.name_localizations),
        "description": description,
        **_localized("description_localizations", settings.description_localizations),
        "required": parameter.default is inspect.Parameter.empty and not optional,
    }
    optional_fields = {
        "choices": [_choice(choice) for choice in choices] or None,
        "autocomplete": True if settings.autocomplete is not None else None,
        "min_value": settings.min,
        "max_value": settings.max,
        "min_length": settings.min_length,
        "max_length": settings.max_length,
        "channel_types": None
        if settings.channel_types is None
        else list(settings.channel_types),
        "file_types": None
        if settings.file_types is None
        else list(settings.file_types),
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
        parameter.name,
        payload,
        default,
        models,
        lookup,
        settings.autocomplete,
        converter,
    )


def _value_hint(converter: Callable[[Any, Any], Any]) -> object:
    try:
        parameters = list(inspect.signature(converter).parameters)
        hints = typing.get_type_hints(converter, include_extras=True)
    except (TypeError, ValueError):
        parameters, hints = [], {}
    if len(parameters) != CONVERTER_ARITY or parameters[-1] not in hints:
        raise CommandDefinitionError(
            "Convert takes a function of the context and a value with a type hint"
        )
    return hints[parameters[-1]]


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
) -> tuple[
    OptionType, list[Choice], tuple[OptionModel, ...], Callable[[Any], object] | None
]:
    models = tuple(
        model for member in members for model in OPTION_MODELS if member is model
    )
    if len(models) == len(members):
        kinds = frozenset(OPTION_MODELS[model] for model in models)
        if len(models) == 1:
            return OPTION_MODELS[models[0]], [], models, None
        if kinds == MENTIONABLE_TYPES:
            return OptionType.MENTIONABLE, [], models, None
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
    advice = [use for model, use in MODEL_ADVICE.items() if model in members]
    raise CommandDefinitionError(
        "; ".join(
            [
                "unsupported type hint "
                + " | ".join(map(inspect.formatannotation, members)),
                *advice,
            ]
        )
    )


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
    for localized in payload.get("name_localizations", {}).values():
        _check_name(localized)
    for localized in payload.get("description_localizations", {}).values():
        _check_description(localized)
    for key, allowed in FIELD_TYPES.items():
        if key in payload and option_type not in allowed:
            raise CommandDefinitionError(
                f"{key} does not apply to {option_type.name} options"
            )
    if "choices" in payload and "autocomplete" in payload:
        raise CommandDefinitionError("autocomplete cannot be combined with choices")
    if len(payload.get("file_types", ())) > MAX_FILE_TYPES:
        raise CommandDefinitionError("file_types takes at most 10 types")
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


def _choice(choice: Choice) -> dict[str, Any]:
    return {
        "name": choice.name,
        **_localized("name_localizations", choice.name_localizations),
        "value": choice.value,
    }


def _localized(key: str, texts: Mapping[str, str] | None) -> dict[str, Any]:
    return {key: dict(texts)} if texts else {}


def _check_name(name: str, *, spaced: bool = False) -> None:
    if not (
        1 <= len(name) <= MAX_NAME
        and all(_is_name_character(character, spaced=spaced) for character in name)
    ):
        letters = "letters, digits, spaces" if spaced else "lowercase letters, digits"
        raise CommandDefinitionError(
            f"{name!r} is not a valid name: use 1-32 {letters}, '-' or '_'"
        )


def _is_name_character(character: str, *, spaced: bool) -> bool:
    if character in NAME_SYMBOLS or (spaced and character == " "):
        return True
    if not spaced and character != character.lower():
        return False
    return unicodedata.category(character)[0] in "LN" or any(
        ord(character) in script for script in NAME_SCRIPTS
    )


def _check_description(description: str) -> None:
    if not 1 <= len(description) <= MAX_TEXT:
        raise CommandDefinitionError(
            f"description {description!r} must be 1-100 characters long"
        )


def _command_description(callback: CommandCallback, description: str | None) -> str:
    if description is not None:
        return description
    docstring = inspect.getdoc(callback)
    return docstring.splitlines()[0] if docstring else DEFAULT_DESCRIPTION


def _documented(callback: CommandCallback) -> dict[str, str]:
    lines = (inspect.getdoc(callback) or "").splitlines()
    for index, line in enumerate(lines):
        following = lines[index + 1 : index + 2]
        if line.strip() == "Args:":
            return _described(lines[index + 1 :], _indent(line), google=True)
        if (
            line.strip() == "Parameters"
            and following
            and set(following[0].strip()) == {"-"}
        ):
            return _described(lines[index + 2 :], _indent(line) - 1, google=False)
    return {}


def _described(lines: list[str], header: int, *, google: bool) -> dict[str, str]:
    found: dict[str, str] = {}
    entry: int | None = None
    name: str | None = None
    for line in lines:
        if not line.strip() or _indent(line) <= header:
            break
        text = line.strip()
        entry = _indent(line) if entry is None else entry
        if _indent(line) > entry and name is not None:
            found[name] = f"{found[name]} {text}".strip()
        elif google:
            match = GOOGLE_ENTRY.fullmatch(text)
            if match is None:
                break
            name, found[name] = match.group("name", "text")
        else:
            name = text.split(":")[0].strip()
            found[name] = ""
    return found


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _warn_if_over_budget(node: _Node) -> None:
    while node.parent is not None:
        node = node.parent
    size = _text_size(node.payload("guild"))
    if size > TEXT_BUDGET:
        warn_user(
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
