import re
from collections.abc import Callable
from datetime import datetime
from typing import Literal

type TimestampStyle = Literal["t", "T", "d", "D", "f", "F", "s", "S", "R"]
type GuildNavigation = Literal["customize", "browse", "guide", "linked-roles"]

LINK = re.compile(r"(https?://\S+)")
MARKDOWN = re.compile(r"([\\*_~`|>#\-\[\]()])|^(\s*\d+)\.", re.MULTILINE)
INLINE_MARKDOWN = re.compile(r"[\\*_~`|]")
LINE_MARKDOWN = re.compile(r"^(?:>>> |> |#{1,3} |-# |[-*] )", re.MULTILINE)
PING = re.compile(r"@(everyone|here|[!&]?\d{17,20})")
USER_MENTION = re.compile(r"<@!?(\d{15,20})>")
ROLE_MENTION = re.compile(r"<@&(\d{15,20})>")
CHANNEL_MENTION = re.compile(r"<#(\d{15,20})>")


def timestamp(when: datetime | float, style: TimestampStyle | None = None) -> str:
    """A time each reader sees in their own time zone and language."""
    seconds = int(when.timestamp() if isinstance(when, datetime) else when)
    return f"<t:{seconds}>" if style is None else f"<t:{seconds}:{style}>"


def user(user_id: int) -> str:
    return f"<@{user_id}>"


def channel(channel_id: int) -> str:
    return f"<#{channel_id}>"


def role(role_id: int) -> str:
    return f"<@&{role_id}>"


def game_profile(game_id: int) -> str:
    return f"<@${game_id}>"


def command(name: str, command_id: int) -> str:
    """A clickable slash command; `name` may hold a group and subcommand."""
    return f"</{name}:{command_id}>"


def emoji(name: str, emoji_id: int, *, animated: bool = False) -> str:
    return f"<{'a' if animated else ''}:{name}:{emoji_id}>"


def guild_navigation(kind: GuildNavigation, role_id: int | None = None) -> str:
    """A link to a tab of the server; `role_id` opens one linked role."""
    if role_id is None:
        return f"<id:{kind}>"
    if kind != "linked-roles":
        raise ValueError(f"only linked-roles takes a role_id, not {kind!r}")
    return f"<id:linked-roles:{role_id}>"


def escape_markdown(text: str) -> str:
    """The text shown as typed, with links left working."""
    return _outside_links(text, lambda part: MARKDOWN.sub(_escaped, part))


def remove_markdown(text: str) -> str:
    """The text without markdown, with links left working."""
    return _outside_links(
        text, lambda part: INLINE_MARKDOWN.sub("", LINE_MARKDOWN.sub("", part))
    )


def escape_mentions(text: str) -> str:
    """The text with @everyone, @here and user and role mentions that cannot ping."""
    return PING.sub("@\u200b\\1", text)


def mentioned_users(text: str) -> list[int]:
    return [int(found) for found in USER_MENTION.findall(text)]


def mentioned_roles(text: str) -> list[int]:
    return [int(found) for found in ROLE_MENTION.findall(text)]


def mentioned_channels(text: str) -> list[int]:
    return [int(found) for found in CHANNEL_MENTION.findall(text)]


def _escaped(match: re.Match[str]) -> str:
    symbol, number = match.groups()
    return f"\\{symbol}" if symbol is not None else f"{number}\\."


def _outside_links(text: str, change: Callable[[str], str]) -> str:
    parts = LINK.split(text)
    return "".join(
        part if index % 2 else change(part) for index, part in enumerate(parts)
    )
