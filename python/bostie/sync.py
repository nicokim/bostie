import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bostie.commands import (
    Command,
    CommandDefaults,
    ContextMenuCommand,
    Group,
    Scope,
    _python_name,
    _warn,
)
from bostie.enums import CommandType, InteractionContextType, OptionType
from bostie.errors import BadRequest, CommandSyncError

if TYPE_CHECKING:
    from bostie.http import HTTPClient

log = logging.getLogger("bostie.sync")

GLOBAL_ROUTE = "/applications/{application_id}/commands"
GUILD_ROUTE = "/applications/{application_id}/guilds/{guild_id}/commands"
DROPPED_REMOTE_KEYS = frozenset(
    {"application_id", "version", "guild_id", "dm_permission", "default_permission"}
)
NESTING_TYPES = frozenset({OptionType.SUB_COMMAND, OptionType.SUB_COMMAND_GROUP})
LIMITS = {
    CommandType.CHAT_INPUT: ("slash", 100),
    CommandType.USER: ("user", 15),
    CommandType.MESSAGE: ("message", 15),
}
COMMAND_DEFAULTS: dict[str, Any] = {
    "name": None,
    "name_localizations": None,
    "description": "",
    "description_localizations": None,
    "options": [],
    "default_member_permissions": None,
    "nsfw": False,
}
OPTION_DEFAULTS: dict[str, Any] = {
    "type": None,
    "name": None,
    "name_localizations": None,
    "description": None,
    "description_localizations": None,
    "required": False,
    "choices": [],
    "options": [],
    "channel_types": [],
    "file_types": [],
    "min_value": None,
    "max_value": None,
    "min_length": None,
    "max_length": None,
    "autocomplete": False,
}


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Commands that differed in one scope; `guild_id` is None for global."""

    guild_id: int | None
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


async def sync_commands(
    http: "HTTPClient",
    application_id: int,
    commands: Sequence[Command | Group | ContextMenuCommand],
    guild_ids: Iterable[int],
    *,
    defaults: CommandDefaults | None = None,
    always_put: bool = False,
) -> list[SyncResult]:
    """Make Discord's commands match `commands`, one PUT per differing scope.

    Commands of a type bostie does not define, such as an Activity's entry point,
    are kept as they are.
    """
    if not commands and not always_put:
        return []
    base: list[int | None] = [*guild_ids] or [None]
    scopes: dict[int | None, list[Command | Group | ContextMenuCommand]] = {
        scope: [] for scope in base
    }
    for command in commands:
        for scope in command.guilds or base:
            scopes.setdefault(scope, []).append(command)
    for scoped in scopes.values():
        for type, count in Counter(command.type for command in scoped).items():
            label, limit = LIMITS[type]
            if count > limit:
                _warn(f"{count} {label} commands; Discord allows {limit} per scope")
    return [
        await _sync_scope(
            http,
            application_id,
            scoped,
            guild_id,
            defaults=defaults,
            always_put=always_put,
        )
        for guild_id, scoped in scopes.items()
    ]


async def _sync_scope(
    http: "HTTPClient",
    application_id: int,
    commands: Sequence[Command | Group | ContextMenuCommand],
    guild_id: int | None,
    *,
    defaults: CommandDefaults | None,
    always_put: bool,
) -> SyncResult:
    scope: Scope
    params: dict[str, Any]
    if guild_id is None:
        scope, route, label = "global", GLOBAL_ROUTE, "global"
        params = {"application_id": application_id}
    else:
        scope, route, label = "guild", GUILD_ROUTE, f"guild {guild_id}"
        params = {"application_id": application_id, "guild_id": guild_id}
    local = [command.payload(scope, defaults) for command in commands]
    remote = await http.request(
        "GET", route, query={"with_localizations": True}, **params
    )
    ours = {_key(command): command for command in remote if _key(command)[0] in LIMITS}
    passthrough = [
        _passthrough(command) for command in remote if _key(command)[0] not in LIMITS
    ]
    result = _diff(guild_id, local, ours)
    if not (result.added or result.removed or result.changed or always_put):
        log.info("commands up to date (%s)", label)
        return result
    body = local + passthrough
    try:
        await http.request("PUT", route, json=body, **params)
    except BadRequest as error:
        raise _sync_error(error, body, commands) from error
    log.info(
        "commands synced (%s): added %s, removed %s, changed %s",
        label,
        list(result.added),
        list(result.removed),
        list(result.changed),
    )
    return result


def _passthrough(command: Mapping[str, Any]) -> dict[str, Any]:
    kept = {
        key: value for key, value in command.items() if key not in DROPPED_REMOTE_KEYS
    }
    if (
        command.get("default_permission") is False
        and command.get("default_member_permissions") is None
    ):
        kept["default_member_permissions"] = "0"
    if (
        command.get("dm_permission") is False
        and command.get("contexts") is None
        and command.get("guild_id") is None
    ):
        kept["contexts"] = [InteractionContextType.GUILD]
    return kept


def _key(command: Mapping[str, Any]) -> tuple[int, str]:
    return command.get("type", CommandType.CHAT_INPUT), command["name"]


def _diff(
    guild_id: int | None,
    local: Sequence[Mapping[str, Any]],
    remote: Mapping[tuple[int, str], Mapping[str, Any]],
) -> SyncResult:
    by_key = {_key(command): command for command in local}
    return SyncResult(
        guild_id,
        added=tuple(key[1] for key in by_key if key not in remote),
        removed=tuple(key[1] for key in remote if key not in by_key),
        changed=tuple(
            key[1]
            for key, command in by_key.items()
            if key in remote
            and _canonical_command(remote[key], command)
            != _canonical_command(command, command)
        ),
    )


def _canonical_command(
    command: Mapping[str, Any], local: Mapping[str, Any]
) -> dict[str, Any]:
    canonical = _project(command, local, COMMAND_DEFAULTS)
    canonical["options"] = _canonical_options(
        canonical["options"], local.get("options", [])
    )
    return canonical


def _canonical_options(
    options: Sequence[Mapping[str, Any]], local: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    return [
        _canonical_option(option, local[index] if index < len(local) else {})
        for index, option in enumerate(options)
    ]


def _canonical_option(
    option: Mapping[str, Any], local: Mapping[str, Any]
) -> dict[str, Any]:
    canonical = _project(option, local, OPTION_DEFAULTS)
    canonical["choices"] = [
        {
            "name": choice["name"],
            "name_localizations": choice.get("name_localizations") or None,
            "value": choice["value"],
        }
        for choice in canonical["choices"]
    ]
    canonical["channel_types"] = sorted(canonical["channel_types"])
    canonical["options"] = _canonical_options(
        canonical["options"], local.get("options", [])
    )
    return canonical


def _project(
    payload: Mapping[str, Any], local: Mapping[str, Any], defaults: Mapping[str, Any]
) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    for key in defaults.keys() | local.keys():
        value = payload.get(key)
        canonical[key] = defaults.get(key) if value in (None, {}) else value
    return canonical


def _sync_error(
    error: BadRequest,
    body: Sequence[Mapping[str, Any]],
    commands: Sequence[Command | Group | ContextMenuCommand],
) -> CommandSyncError:
    path, message = _first_error(error.errors) or ((), error.message)
    if not path:
        return CommandSyncError("", "", message)
    index, rest = int(path[0]), path[1:]
    node = body[index]
    slash = _key(node)[0] == CommandType.CHAT_INPUT
    names = [node["name"]]
    option = ""
    while len(rest) > 1 and rest[0] == "options":
        node = node["options"][int(rest[1])]
        rest = rest[2:]
        if node["type"] in NESTING_TYPES:
            names.append(node["name"])
        else:
            option = f"option {node['name']!r}"
    command = ("/" if slash else "") + " ".join(names)
    callback_name = (
        _callback_name(commands[index], names[1:]) if index < len(commands) else None
    )
    if callback_name is not None:
        command += f" ({callback_name})"
    field = ".".join(rest)
    return CommandSyncError(
        command, option, f"{field}: {message}" if field else message
    )


def _first_error(
    tree: Mapping[str, Any], path: tuple[str, ...] = ()
) -> tuple[tuple[str, ...], str] | None:
    for key, value in tree.items():
        if key == "_errors":
            return path, " ".join(error["message"] for error in value)
        found = _first_error(value, (*path, key))
        if found is not None:
            return found
    return None


def _callback_name(
    node: Command | Group | ContextMenuCommand, names: Sequence[str]
) -> str | None:
    for name in names:
        if not isinstance(node, Group):
            return None
        node = node.commands[name]
    if isinstance(node, Group):
        return None
    return _python_name(node.callback)
