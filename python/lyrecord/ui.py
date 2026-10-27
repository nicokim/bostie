from collections.abc import Iterable, Mapping
from typing import Any

from lyrecord.custom_id import MAX_CUSTOM_ID
from lyrecord.enums import ButtonStyle, ComponentType

__all__ = ["ButtonStyle", "button", "link_button", "row", "select", "select_option"]

type Emoji = str | Mapping[str, Any]


def row(*components: Mapping[str, Any]) -> dict[str, Any]:
    """An action row holding up to five buttons or one select menu."""
    return {"type": ComponentType.ACTION_ROW, "components": list(components)}


def button(
    label: str | None,
    *,
    custom_id: str,
    style: ButtonStyle = ButtonStyle.SECONDARY,
    emoji: Emoji | None = None,
    disabled: bool = False,
) -> dict[str, Any]:
    """A button that sends `custom_id` to the bot when clicked."""
    return _present(
        type=ComponentType.BUTTON,
        style=style,
        label=label,
        emoji=_emoji(emoji),
        custom_id=_checked(custom_id),
        disabled=disabled,
    )


def link_button(
    label: str | None, url: str, *, emoji: Emoji | None = None, disabled: bool = False
) -> dict[str, Any]:
    """A button that opens `url` and never reaches the bot."""
    return _present(
        type=ComponentType.BUTTON,
        style=ButtonStyle.LINK,
        label=label,
        emoji=_emoji(emoji),
        url=url,
        disabled=disabled,
    )


def select(
    custom_id: str,
    options: Iterable[Mapping[str, Any]],
    *,
    placeholder: str | None = None,
    min_values: int = 1,
    max_values: int = 1,
    disabled: bool = False,
) -> dict[str, Any]:
    """A string select menu; build its options with `select_option`."""
    return _present(
        type=ComponentType.STRING_SELECT,
        custom_id=_checked(custom_id),
        options=list(options),
        placeholder=placeholder,
        min_values=min_values,
        max_values=max_values,
        disabled=disabled,
    )


def select_option(
    label: str,
    value: str,
    *,
    description: str | None = None,
    emoji: Emoji | None = None,
    default: bool = False,
) -> dict[str, Any]:
    """One entry of a select menu; `value` is what the bot receives."""
    return _present(
        label=label,
        value=value,
        description=description,
        emoji=_emoji(emoji),
        default=default,
    )


def _present(**fields: Any) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


def _emoji(emoji: Emoji | None) -> Mapping[str, Any] | None:
    return {"name": emoji} if isinstance(emoji, str) else emoji


def _checked(custom_id: str) -> str:
    if not 1 <= len(custom_id) <= MAX_CUSTOM_ID:
        raise ValueError(
            f"custom_id must be 1-100 characters, got {len(custom_id)}: {custom_id!r}"
        )
    return custom_id
