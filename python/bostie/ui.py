from collections.abc import Iterable, Mapping
from typing import Any, Literal

from bostie.custom_id import MAX_CUSTOM_ID
from bostie.enums import ButtonStyle, ComponentType

__all__ = [
    "ButtonStyle",
    "button",
    "container",
    "file",
    "gallery_item",
    "link_button",
    "media_gallery",
    "row",
    "section",
    "select",
    "select_option",
    "separator",
    "text",
    "thumbnail",
]

type Emoji = str | Mapping[str, Any]
type InteractiveStyle = Literal[
    ButtonStyle.PRIMARY, ButtonStyle.SECONDARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER
]


def row(*components: Mapping[str, Any]) -> dict[str, Any]:
    """An action row holding up to five buttons or one select menu."""
    return {"type": ComponentType.ACTION_ROW, "components": list(components)}


def button(
    label: str | None,
    *,
    custom_id: str,
    style: InteractiveStyle = ButtonStyle.SECONDARY,
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


def text(content: str) -> dict[str, Any]:
    """Markdown text; Components V2 messages use it instead of `content`."""
    return {"type": ComponentType.TEXT_DISPLAY, "content": content}


def section(*texts: Mapping[str, Any], accessory: Mapping[str, Any]) -> dict[str, Any]:
    """One to three `text` components with a `button` or `thumbnail` beside them."""
    return {
        "type": ComponentType.SECTION,
        "components": list(texts),
        "accessory": accessory,
    }


def thumbnail(
    url: str, *, description: str | None = None, spoiler: bool = False
) -> dict[str, Any]:
    """A small image for a `section`; `url` may be `attachment://<filename>`."""
    return _present(
        type=ComponentType.THUMBNAIL,
        media={"url": url},
        description=description,
        spoiler=spoiler,
    )


def media_gallery(*items: Mapping[str, Any]) -> dict[str, Any]:
    """One to ten images or videos built with `gallery_item`."""
    return {"type": ComponentType.MEDIA_GALLERY, "items": list(items)}


def gallery_item(
    url: str, *, description: str | None = None, spoiler: bool = False
) -> dict[str, Any]:
    """One entry of a `media_gallery`; `url` may be `attachment://<filename>`."""
    return _present(media={"url": url}, description=description, spoiler=spoiler)


def file(filename: str, *, spoiler: bool = False) -> dict[str, Any]:
    """A file uploaded with the message, named as in its `File`."""
    return {
        "type": ComponentType.FILE,
        "file": {"url": f"attachment://{filename}"},
        "spoiler": spoiler,
    }


def separator(*, divider: bool = True, spacing: Literal[1, 2] = 1) -> dict[str, Any]:
    """Space between components; `spacing=2` is large, and `divider` draws a line."""
    return {"type": ComponentType.SEPARATOR, "divider": divider, "spacing": spacing}


def container(
    *components: Mapping[str, Any],
    accent_color: int | None = None,
    spoiler: bool = False,
) -> dict[str, Any]:
    """A box around rows, texts, sections, galleries, files and separators."""
    return _present(
        type=ComponentType.CONTAINER,
        components=list(components),
        accent_color=accent_color,
        spoiler=spoiler,
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
