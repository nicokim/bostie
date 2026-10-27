import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lyrecord.commands import Command, Group, Scope, _python_name, _warn
from lyrecord.enums import CommandType, InteractionContextType, OptionType
from lyrecord.errors import BadRequest, CommandSyncError

if TYPE_CHECKING:
    from lyrecord.http import HTTPClient

log = logging.getLogger("lyrecord.sync")

GLOBAL_ROUTE = "/applications/{application_id}/commands"
GUILD_ROUTE = "/applications/{application_id}/guilds/{guild_id}/commands"
DROPPED_REMOTE_KEYS = frozenset(
    {"application_id", "version", "guild_id", "dm_permission", "default_permission"}
)
NESTING_TYPES = frozenset({OptionType.SUB_COMMAND, OptionType.SUB_COMMAND_GROUP})
MAX_COMMANDS = 100
COMMAND_DEFAULTS: dict[str, Any] = {
    "name": None,
    "description": None,
    "options": [],
    "default_member_permissions": None,
    "nsfw": False,
}
OPTION_DEFAULTS: dict[str, Any] = {
    "type": None,
    "name": None,
    "description": None,
    "required": False,
    "choices": [],
    "options": [],
    "channel_types": [],
    "min_value": None,
    "max_value": None,
    "min_length": None,
    "max_length": None,
    "autocomplete": False,
}


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Slash commands that differed in one scope; `guild_id` is None for global."""

    guild_id: int | None
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


async def sync_commands(
    http: Any,
    application_id: int,
    commands: Sequence[Command | Group],
    guild_ids: Iterable[int],
    *,
    always_put: bool = False,
) -> list[SyncResult]:
    """Make Discord's slash commands match `commands`, one PUT per differing scope."""
    if not commands and not always_put:
        return []
    if len(commands) > MAX_COMMANDS:
        _warn(f"{len(commands)} slash commands; Discord allows 100 per scope")
    scopes: list[int | None] = [*guild_ids] or [None]
    return [
        await _sync_scope(http, application_id, commands, guild_id, always_put)
        for guild_id in scopes
    ]


async def _sync_scope(
    http: "HTTPClient",
    application_id: int,
    commands: Sequence[Command | Group],
    guild_id: int | None,
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
    local = [command.payload(scope) for command in commands]
    remote = await http.request(
        "GET", route, query={"with_localizations": True}, **params
    )
    ours = {
        command["name"]: command
        for command in remote
        if command.get("type", CommandType.CHAT_INPUT) == CommandType.CHAT_INPUT
    }
    passthrough = [
        _passthrough(command)
        for command in remote
        if command.get("type", CommandType.CHAT_INPUT) != CommandType.CHAT_INPUT
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


def _diff(
    guild_id: int | None,
    local: Sequence[Mapping[str, Any]],
    remote: Mapping[str, Mapping[str, Any]],
) -> SyncResult:
    by_name = {command["name"]: command for command in local}
    return SyncResult(
        guild_id,
        added=tuple(name for name in by_name if name not in remote),
        removed=tuple(name for name in remote if name not in by_name),
        changed=tuple(
            name
            for name, command in by_name.items()
            if name in remote
            and _canonical_command(remote[name], command)
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
        {"name": choice["name"], "value": choice["value"]}
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
    canonical = {}
    for key in defaults.keys() | local.keys():
        value = payload.get(key)
        canonical[key] = defaults.get(key) if value is None else value
    return canonical


def _sync_error(
    error: BadRequest,
    body: Sequence[Mapping[str, Any]],
    commands: Sequence[Command | Group],
) -> CommandSyncError:
    path, message = _first_error(error.errors) or ((), error.message)
    if not path:
        return CommandSyncError("", "", message)
    index, rest = int(path[0]), path[1:]
    node = body[index]
    names = [node["name"]]
    option = ""
    while len(rest) >= 2 and rest[0] == "options":
        node = node["options"][int(rest[1])]
        rest = rest[2:]
        if node["type"] in NESTING_TYPES:
            names.append(node["name"])
        else:
            option = f"option {node['name']!r}"
    command = "/" + " ".join(names)
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


def _callback_name(node: Command | Group, names: Sequence[str]) -> str | None:
    for name in names:
        if not isinstance(node, Group):
            return None
        node = node.commands[name]
    if isinstance(node, Group):
        return None
    return _python_name(node.callback)
