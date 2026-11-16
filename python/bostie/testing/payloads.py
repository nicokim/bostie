import itertools
import secrets
import time
from pathlib import Path
from typing import Any

import msgspec

type Json = dict[str, Any]

_FIXTURES = Path(__file__).parent / "fixtures"
_DISCORD_EPOCH_MS = 1_420_070_400_000
_INTERACTION_FIXTURES = {2: "command", 3: "button", 4: "autocomplete", 5: "modal"}
_GUILD_VOICE = 2
_STAGE_CHANNEL_ID = "1335174052137131073"

_increments = itertools.count()


def fixture(name: str) -> Json:
    """A fresh copy of the documented payload `fixtures/{name}.json`."""
    return msgspec.json.decode(
        (_FIXTURES / f"{name}.json").read_bytes(), type=dict[str, Any]
    )


def snowflake(at: float | None = None) -> str:
    """A unique snowflake created at `at` (Unix seconds, now by default)."""
    millis = int((time.time() if at is None else at) * 1000) - _DISCORD_EPOCH_MS
    return str(millis << 22 | next(_increments) % 4096)


def user(**fields: Any) -> Json:
    """A user, as `GET /users/{id}` returns it."""
    return fixture("rest/user") | fields


def member(**fields: Any) -> Json:
    """A guild member with its user."""
    return fixture("rest/member") | fields


def role(**fields: Any) -> Json:
    """A guild role."""
    data: Json = fixture("events/GUILD_ROLE_CREATE")["role"]
    return data | fields


def channel(**fields: Any) -> Json:
    """A guild text channel."""
    return fixture("rest/channel") | fields


def guild(**fields: Any) -> Json:
    """A GUILD_CREATE with channels (one obfuscated), a thread, roles and members."""
    return fixture("events/GUILD_CREATE") | fields


def message(**fields: Any) -> Json:
    """A MESSAGE_CREATE payload with a fresh id."""
    return fixture("events/MESSAGE_CREATE") | {"id": snowflake()} | fields


def interaction(type: int, data: Json, **fields: Any) -> Json:
    """An INTERACTION_CREATE payload of `type` with a fresh id and token."""
    base = fixture(
        f"events/INTERACTION_CREATE.{_INTERACTION_FIXTURES.get(type, 'command')}"
    )
    fresh = {"id": snowflake(), "type": type, "token": secrets.token_urlsafe(48)}
    return base | fresh | {"data": data} | fields


def command(
    name: str,
    *options: Json,
    resolved: Json | None = None,
    guild_id: int | None = None,
) -> Json:
    """The `data` of a slash command or autocomplete interaction.

    `guild_id` is the guild the command is registered in, for a guild command.
    """
    data: Json = {"id": snowflake(), "name": name, "type": 1}
    if options:
        data["options"] = list(options)
    if resolved is not None:
        data["resolved"] = resolved
    if guild_id is not None:
        data["guild_id"] = str(guild_id)
    return data


def context_menu(type: int, name: str, target_id: str, resolved: Json) -> Json:
    """The `data` of a user (2) or message (3) command run on `target_id`."""
    return {
        "id": snowflake(),
        "name": name,
        "type": type,
        "target_id": target_id,
        "resolved": resolved,
    }


def group(name: str, *subcommands: Json) -> Json:
    """A subcommand group option."""
    return {"name": name, "type": 2, "options": list(subcommands)}


def subcommand(name: str, *options: Json) -> Json:
    """A subcommand option."""
    return {"name": name, "type": 1, "options": list(options)}


def option(name: str, type: int, value: Any, *, focused: bool = False) -> Json:
    """A command option value; `focused` marks the one being autocompleted."""
    data: Json = {"name": name, "type": type, "value": value}
    if focused:
        data["focused"] = True
    return data


def resolved() -> Json:
    """Resolved users with their members, a role, a channel and an attachment."""
    data: Json = fixture("events/INTERACTION_CREATE.command")["data"]["resolved"]
    return data


def button(custom_id: str) -> Json:
    """The `data` of a button interaction."""
    data: Json = fixture("events/INTERACTION_CREATE.button")["data"]
    return data | {"custom_id": custom_id}


def select(custom_id: str, *values: str) -> Json:
    """The `data` of a string select interaction."""
    data: Json = fixture("events/INTERACTION_CREATE.select")["data"]
    return data | {"custom_id": custom_id, "values": list(values)}


def modal(custom_id: str) -> Json:
    """The `data` of a modal submit."""
    data: Json = fixture("events/INTERACTION_CREATE.modal")["data"]
    return data | {"custom_id": custom_id}


def voice_channel(**fields: Any) -> Json:
    """The guild's voice channel, as `GET /channels/{id}` returns it."""
    guild = fixture("events/GUILD_CREATE")
    channel: Json = next(c for c in guild["channels"] if c["type"] == _GUILD_VOICE)
    return channel | {"guild_id": guild["id"]} | fields


def stage_channel(**fields: Any) -> Json:
    """A stage channel in the same guild."""
    return voice_channel(id=_STAGE_CHANNEL_ID, type=13, name="Town Hall") | fields


def voice_state(**fields: Any) -> Json:
    """A VOICE_STATE_UPDATE payload, by default the bot's own."""
    return fixture("events/VOICE_STATE_UPDATE") | fields
