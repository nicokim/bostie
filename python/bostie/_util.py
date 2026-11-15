import os
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from bostie.enums import OptionType

if TYPE_CHECKING:
    from bostie.models import InteractionDataOption
    from bostie.router import Router

LIBRARY_DIRECTORY = str(Path(__file__).parent)
TOKEN_VARIABLE = "BOSTIE_TOKEN"
SUBCOMMAND_TYPES = (OptionType.SUB_COMMAND, OptionType.SUB_COMMAND_GROUP)


def qualified_name(function: Callable[..., object]) -> str:
    """`module.qualname` of a function, to name user code in logs and errors."""
    return f"{function.__module__}.{function.__qualname__}"


def warn_user(message: str) -> None:
    """Warn at the first line of user code that led here."""
    warnings.warn(message, stacklevel=2, skip_file_prefixes=(LIBRARY_DIRECTORY,))


def resolve_token(token: str | None) -> str:
    """The given token, else BOSTIE_TOKEN, without a "Bot " prefix."""
    token = token or os.environ.get(TOKEN_VARIABLE)
    if not token:
        raise ValueError(f"pass a token or set {TOKEN_VARIABLE}")
    return token.removeprefix("Bot ")


def split_subcommands(
    options: tuple["InteractionDataOption", ...],
) -> tuple[list[str], tuple["InteractionDataOption", ...]]:
    """The subcommand names of an invocation, and the options of the leaf command."""
    names: list[str] = []
    while options and options[0].type in SUBCOMMAND_TYPES:
        names.append(options[0].name)
        options = options[0].options
    return names, options


def routers_from(router: "Router") -> Iterator["Router"]:
    """`router`, then each router it is included in, out to the bot."""
    current: Router | None = router
    while current is not None:
        yield current
        current = current.parent
