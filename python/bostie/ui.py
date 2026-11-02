from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, NewType, NotRequired, TypedDict, cast

from bostie.custom_id import MAX_CUSTOM_ID, ComponentRoute
from bostie.enums import ButtonStyle, ChannelType, ComponentType, TextInputStyle
from bostie.models.components import SelectDefaultType

__all__ = [
    "ButtonDict",
    "ButtonStyle",
    "CheckboxDict",
    "CheckboxGroupDict",
    "ContainerDict",
    "EntitySelectDict",
    "FileDict",
    "FileUploadDict",
    "GalleryItemDict",
    "GroupOptionDict",
    "LabelDict",
    "MediaGalleryDict",
    "ModalDict",
    "RadioGroupDict",
    "RawComponent",
    "RowDict",
    "SectionDict",
    "SelectDefaultDict",
    "SelectOptionDict",
    "SeparatorDict",
    "StringSelectDict",
    "TextDict",
    "TextInputDict",
    "TextInputStyle",
    "ThumbnailDict",
    "button",
    "channel_select",
    "checkbox",
    "checkbox_group",
    "container",
    "file",
    "file_upload",
    "gallery_item",
    "group_option",
    "label",
    "link_button",
    "media_gallery",
    "mentionable_select",
    "modal",
    "pager",
    "premium_button",
    "radio_group",
    "raw",
    "role_select",
    "row",
    "section",
    "select_option",
    "separator",
    "string_select",
    "text",
    "text_input",
    "thumbnail",
    "user_select",
]

MAX_BUTTON_LABEL = 80
MAX_URL = 512
MAX_ROW_BUTTONS = 5
MAX_SELECT_OPTIONS = 25
MAX_PLACEHOLDER = 150
MAX_OPTION_TEXT = 100
MAX_SECTION_TEXTS = 3
MAX_GALLERY_ITEMS = 10
MAX_ALT_TEXT = 1024
MAX_MODAL_TITLE = 45
MAX_MODAL_COMPONENTS = 5
MAX_LABEL = 45
MAX_LABEL_DESCRIPTION = 100
MAX_INPUT = 4000
MAX_INPUT_PLACEHOLDER = 100
MAX_UPLOADS = 10
MAX_FILE_TYPES = 10
MAX_GROUP_OPTIONS = 10
SELECT_TYPES = frozenset(
    {
        ComponentType.STRING_SELECT,
        ComponentType.USER_SELECT,
        ComponentType.ROLE_SELECT,
        ComponentType.MENTIONABLE_SELECT,
        ComponentType.CHANNEL_SELECT,
    }
)

type _Emoji = str | Mapping[str, Any]
RawComponent = NewType("RawComponent", dict[str, Any])
type InteractiveStyle = Literal[
    ButtonStyle.PRIMARY, ButtonStyle.SECONDARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER
]
type _EntitySelectType = Literal[
    ComponentType.USER_SELECT,
    ComponentType.ROLE_SELECT,
    ComponentType.MENTIONABLE_SELECT,
    ComponentType.CHANNEL_SELECT,
]


class ButtonDict(TypedDict):
    type: Literal[ComponentType.BUTTON]
    id: NotRequired[int]
    style: ButtonStyle
    label: NotRequired[str]
    emoji: NotRequired[Mapping[str, Any]]
    custom_id: NotRequired[str]
    sku_id: NotRequired[int]
    url: NotRequired[str]
    disabled: bool


class SelectOptionDict(TypedDict):
    label: str
    value: str
    description: NotRequired[str]
    emoji: NotRequired[Mapping[str, Any]]
    default: bool


class StringSelectDict(TypedDict):
    type: Literal[ComponentType.STRING_SELECT]
    id: NotRequired[int]
    custom_id: str
    options: list[SelectOptionDict]
    placeholder: NotRequired[str]
    min_values: int
    max_values: int
    required: NotRequired[bool]
    disabled: bool


class SelectDefaultDict(TypedDict):
    id: int
    type: SelectDefaultType


class EntitySelectDict(TypedDict):
    type: _EntitySelectType
    id: NotRequired[int]
    custom_id: str
    channel_types: NotRequired[list[ChannelType]]
    placeholder: NotRequired[str]
    default_values: list[SelectDefaultDict]
    min_values: int
    max_values: int
    required: NotRequired[bool]
    disabled: bool


type _RowChild = ButtonDict | StringSelectDict | EntitySelectDict | RawComponent


class RowDict(TypedDict):
    type: Literal[ComponentType.ACTION_ROW]
    id: NotRequired[int]
    components: list[_RowChild]


class TextDict(TypedDict):
    type: Literal[ComponentType.TEXT_DISPLAY]
    id: NotRequired[int]
    content: str


class ThumbnailDict(TypedDict):
    type: Literal[ComponentType.THUMBNAIL]
    id: NotRequired[int]
    media: dict[str, str]
    description: NotRequired[str]
    spoiler: bool


class SectionDict(TypedDict):
    type: Literal[ComponentType.SECTION]
    id: NotRequired[int]
    components: list[TextDict | RawComponent]
    accessory: ButtonDict | ThumbnailDict | RawComponent


class GalleryItemDict(TypedDict):
    media: dict[str, str]
    description: NotRequired[str]
    spoiler: bool


class MediaGalleryDict(TypedDict):
    type: Literal[ComponentType.MEDIA_GALLERY]
    id: NotRequired[int]
    items: list[GalleryItemDict | RawComponent]


class FileDict(TypedDict):
    type: Literal[ComponentType.FILE]
    id: NotRequired[int]
    file: dict[str, str]
    spoiler: bool


class SeparatorDict(TypedDict):
    type: Literal[ComponentType.SEPARATOR]
    id: NotRequired[int]
    divider: bool
    spacing: Literal[1, 2]


type _ContainerChild = (
    RowDict
    | TextDict
    | SectionDict
    | MediaGalleryDict
    | FileDict
    | SeparatorDict
    | RawComponent
)


class ContainerDict(TypedDict):
    type: Literal[ComponentType.CONTAINER]
    id: NotRequired[int]
    components: list[_ContainerChild]
    accent_color: NotRequired[int]
    spoiler: bool


class TextInputDict(TypedDict):
    type: Literal[ComponentType.TEXT_INPUT]
    id: NotRequired[int]
    custom_id: str
    style: TextInputStyle
    min_length: NotRequired[int]
    max_length: NotRequired[int]
    required: bool
    value: NotRequired[str]
    placeholder: NotRequired[str]


class FileUploadDict(TypedDict):
    type: Literal[ComponentType.FILE_UPLOAD]
    id: NotRequired[int]
    custom_id: str
    min_values: int
    max_values: int
    required: bool
    file_types: NotRequired[list[str]]


class GroupOptionDict(TypedDict):
    value: str
    label: str
    description: NotRequired[str]
    default: bool


class RadioGroupDict(TypedDict):
    type: Literal[ComponentType.RADIO_GROUP]
    id: NotRequired[int]
    custom_id: str
    options: list[GroupOptionDict]
    required: bool


class CheckboxGroupDict(TypedDict):
    type: Literal[ComponentType.CHECKBOX_GROUP]
    id: NotRequired[int]
    custom_id: str
    options: list[GroupOptionDict]
    min_values: int
    max_values: NotRequired[int]
    required: bool


class CheckboxDict(TypedDict):
    type: Literal[ComponentType.CHECKBOX]
    id: NotRequired[int]
    custom_id: str
    default: bool


type _LabelChild = (
    TextInputDict
    | StringSelectDict
    | EntitySelectDict
    | FileUploadDict
    | RadioGroupDict
    | CheckboxGroupDict
    | CheckboxDict
    | RawComponent
)


class LabelDict(TypedDict):
    type: Literal[ComponentType.LABEL]
    id: NotRequired[int]
    label: str
    description: NotRequired[str]
    component: _LabelChild


type _ModalChild = LabelDict | TextDict | RawComponent


class ModalDict(TypedDict):
    custom_id: str
    title: str
    components: list[_ModalChild]


def raw(component: Mapping[str, Any]) -> RawComponent:
    """A component ui has no builder for, sent as given."""
    return RawComponent(dict(component))


def row(*components: _RowChild, id: int | None = None) -> RowDict:
    """An action row holding up to five buttons or one select menu."""
    selects = [child for child in components if child.get("type") in SELECT_TYPES]
    if selects and len(components) > 1:
        raise ValueError("a row holds one select menu and nothing else")
    _count("a row", components, 1, MAX_ROW_BUTTONS)
    row: RowDict = {"type": ComponentType.ACTION_ROW, "components": list(components)}
    return _with(row, id=id)


def button(
    label: str | None,
    *,
    custom_id: str,
    style: InteractiveStyle = ButtonStyle.SECONDARY,
    emoji: _Emoji | None = None,
    disabled: bool = False,
    id: int | None = None,
) -> ButtonDict:
    """A button that sends `custom_id` to the bot when clicked."""
    button: ButtonDict = {
        "type": ComponentType.BUTTON,
        "style": style,
        "custom_id": _checked(custom_id),
        "disabled": disabled,
    }
    return _labelled(button, label, emoji, id)


def pager(
    route: ComponentRoute[...], page: int, count: int, **params: str | int
) -> RowDict:
    """Previous and next buttons around "page / count" for a `{page:int}` route.

    Each button's custom_id is `route.id(page=..., **params)`, so pages survive a
    restart; the first page is 0.
    """
    if not 0 <= page < count:
        raise ValueError(f"page must be 0-{count - 1}, got {page}")
    return row(
        button(
            "\N{LEFTWARDS ARROW}",
            custom_id=route.id(page=page - 1, **params),
            disabled=page == 0,
        ),
        button(
            f"{page + 1} / {count}",
            custom_id=route.id(page=page, **params),
            disabled=True,
        ),
        button(
            "\N{RIGHTWARDS ARROW}",
            custom_id=route.id(page=page + 1, **params),
            disabled=page == count - 1,
        ),
    )


def link_button(
    label: str | None,
    url: str,
    *,
    emoji: _Emoji | None = None,
    disabled: bool = False,
    id: int | None = None,
) -> ButtonDict:
    """A button that opens `url` and never reaches the bot."""
    button: ButtonDict = {
        "type": ComponentType.BUTTON,
        "style": ButtonStyle.LINK,
        "url": _short("url", url, MAX_URL),
        "disabled": disabled,
    }
    return _labelled(button, label, emoji, id)


def premium_button(
    sku_id: int, *, disabled: bool = False, id: int | None = None
) -> ButtonDict:
    """A button that offers the SKU `sku_id` for sale and never reaches the bot."""
    button: ButtonDict = {
        "type": ComponentType.BUTTON,
        "style": ButtonStyle.PREMIUM,
        "sku_id": sku_id,
        "disabled": disabled,
    }
    return _with(button, id=id)


def string_select(
    custom_id: str,
    options: Iterable[SelectOptionDict],
    *,
    placeholder: str | None = None,
    min_values: int = 1,
    max_values: int = 1,
    required: bool = True,
    disabled: bool = False,
    id: int | None = None,
) -> StringSelectDict:
    """A select menu of fixed options; build them with `select_option`."""
    listed = list(options)
    _count("a string select", listed, 1, MAX_SELECT_OPTIONS)
    _check_values(min_values, max_values, MAX_SELECT_OPTIONS, required=required)
    select: StringSelectDict = {
        "type": ComponentType.STRING_SELECT,
        "custom_id": _checked(custom_id),
        "options": listed,
        "min_values": min_values,
        "max_values": max_values,
        "disabled": disabled,
    }
    if not required:
        select["required"] = required
    return _with(
        select,
        placeholder=_short("placeholder", placeholder, MAX_PLACEHOLDER),
        id=id,
    )


def user_select(
    custom_id: str,
    *,
    defaults: Iterable[int] = (),
    placeholder: str | None = None,
    min_values: int = 1,
    max_values: int = 1,
    required: bool = True,
    disabled: bool = False,
    id: int | None = None,
) -> EntitySelectDict:
    """A select menu of users; `defaults` are the ids of users shown as chosen."""
    return _entity_select(
        ComponentType.USER_SELECT,
        custom_id,
        _defaults(defaults, "user"),
        placeholder=placeholder,
        min_values=min_values,
        max_values=max_values,
        required=required,
        disabled=disabled,
        id=id,
    )


def role_select(
    custom_id: str,
    *,
    defaults: Iterable[int] = (),
    placeholder: str | None = None,
    min_values: int = 1,
    max_values: int = 1,
    required: bool = True,
    disabled: bool = False,
    id: int | None = None,
) -> EntitySelectDict:
    """A select menu of roles; `defaults` are the ids of roles shown as chosen."""
    return _entity_select(
        ComponentType.ROLE_SELECT,
        custom_id,
        _defaults(defaults, "role"),
        placeholder=placeholder,
        min_values=min_values,
        max_values=max_values,
        required=required,
        disabled=disabled,
        id=id,
    )


def mentionable_select(
    custom_id: str,
    *,
    default_users: Iterable[int] = (),
    default_roles: Iterable[int] = (),
    placeholder: str | None = None,
    min_values: int = 1,
    max_values: int = 1,
    required: bool = True,
    disabled: bool = False,
    id: int | None = None,
) -> EntitySelectDict:
    """A select menu of users and roles, with these ids shown as chosen."""
    return _entity_select(
        ComponentType.MENTIONABLE_SELECT,
        custom_id,
        _defaults(default_users, "user") + _defaults(default_roles, "role"),
        placeholder=placeholder,
        min_values=min_values,
        max_values=max_values,
        required=required,
        disabled=disabled,
        id=id,
    )


def channel_select(
    custom_id: str,
    *,
    channel_types: Iterable[ChannelType] = (),
    defaults: Iterable[int] = (),
    placeholder: str | None = None,
    min_values: int = 1,
    max_values: int = 1,
    required: bool = True,
    disabled: bool = False,
    id: int | None = None,
) -> EntitySelectDict:
    """A select menu of channels, of `channel_types` only when given."""
    select = _entity_select(
        ComponentType.CHANNEL_SELECT,
        custom_id,
        _defaults(defaults, "channel"),
        placeholder=placeholder,
        min_values=min_values,
        max_values=max_values,
        required=required,
        disabled=disabled,
        id=id,
    )
    types = list(channel_types)
    if types:
        select["channel_types"] = types
    return select


def select_option(
    label: str,
    value: str,
    *,
    description: str | None = None,
    emoji: _Emoji | None = None,
    default: bool = False,
) -> SelectOptionDict:
    """One entry of a select menu; `value` is what the bot receives."""
    option: SelectOptionDict = {
        "label": _short("label", label, MAX_OPTION_TEXT),
        "value": _short("value", value, MAX_OPTION_TEXT),
        "default": default,
    }
    return _with(
        option,
        description=_short("description", description, MAX_OPTION_TEXT),
        emoji=None if emoji is None else _emoji(emoji),
    )


def text(content: str, *, id: int | None = None) -> TextDict:
    """Markdown text; Components V2 messages use it instead of `content`."""
    text: TextDict = {"type": ComponentType.TEXT_DISPLAY, "content": content}
    return _with(text, id=id)


def section(
    *texts: TextDict | RawComponent,
    accessory: ButtonDict | ThumbnailDict | RawComponent,
    id: int | None = None,
) -> SectionDict:
    """One to three `text` components with a `button` or `thumbnail` beside them."""
    _count("a section", texts, 1, MAX_SECTION_TEXTS)
    section: SectionDict = {
        "type": ComponentType.SECTION,
        "components": list(texts),
        "accessory": accessory,
    }
    return _with(section, id=id)


def thumbnail(
    url: str,
    *,
    description: str | None = None,
    spoiler: bool = False,
    id: int | None = None,
) -> ThumbnailDict:
    """A small image for a `section`; `url` may be `attachment://<filename>`."""
    thumbnail: ThumbnailDict = {
        "type": ComponentType.THUMBNAIL,
        "media": {"url": url},
        "spoiler": spoiler,
    }
    return _with(
        thumbnail,
        description=_short("description", description, MAX_ALT_TEXT),
        id=id,
    )


def media_gallery(
    *items: GalleryItemDict | RawComponent, id: int | None = None
) -> MediaGalleryDict:
    """One to ten images or videos built with `gallery_item`."""
    _count("a media gallery", items, 1, MAX_GALLERY_ITEMS)
    gallery: MediaGalleryDict = {
        "type": ComponentType.MEDIA_GALLERY,
        "items": list(items),
    }
    return _with(gallery, id=id)


def gallery_item(
    url: str, *, description: str | None = None, spoiler: bool = False
) -> GalleryItemDict:
    """One entry of a `media_gallery`; `url` may be `attachment://<filename>`."""
    item: GalleryItemDict = {"media": {"url": url}, "spoiler": spoiler}
    return _with(item, description=_short("description", description, MAX_ALT_TEXT))


def file(filename: str, *, spoiler: bool = False, id: int | None = None) -> FileDict:
    """A file uploaded with the message, named as in its `File`."""
    file: FileDict = {
        "type": ComponentType.FILE,
        "file": {"url": f"attachment://{filename}"},
        "spoiler": spoiler,
    }
    return _with(file, id=id)


def separator(
    *, divider: bool = True, spacing: Literal[1, 2] = 1, id: int | None = None
) -> SeparatorDict:
    """Space between components; `spacing=2` is large, and `divider` draws a line."""
    separator: SeparatorDict = {
        "type": ComponentType.SEPARATOR,
        "divider": divider,
        "spacing": spacing,
    }
    return _with(separator, id=id)


def container(
    *components: _ContainerChild,
    accent_color: int | None = None,
    spoiler: bool = False,
    id: int | None = None,
) -> ContainerDict:
    """A box around rows, texts, sections, galleries, files and separators."""
    container: ContainerDict = {
        "type": ComponentType.CONTAINER,
        "components": list(components),
        "spoiler": spoiler,
    }
    return _with(container, accent_color=accent_color, id=id)


def modal(custom_id: str, title: str, *components: _ModalChild) -> ModalDict:
    """A popup form of one to five `label` and `text` components."""
    if not 1 <= len(title) <= MAX_MODAL_TITLE:
        raise ValueError(f"a modal title must be 1-45 characters, got {title!r}")
    _count("a modal", components, 1, MAX_MODAL_COMPONENTS)
    return {
        "custom_id": _checked(custom_id),
        "title": title,
        "components": list(components),
    }


def label(
    label: str,
    component: _LabelChild,
    *,
    description: str | None = None,
    id: int | None = None,
) -> LabelDict:
    """A modal field: `label` and `description` over one input, select or upload."""
    field: LabelDict = {
        "type": ComponentType.LABEL,
        "label": _short("label", label, MAX_LABEL),
        "component": component,
    }
    return _with(
        field,
        description=_short("description", description, MAX_LABEL_DESCRIPTION),
        id=id,
    )


def text_input(
    custom_id: str,
    *,
    style: TextInputStyle = TextInputStyle.SHORT,
    placeholder: str | None = None,
    value: str | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    required: bool = True,
    id: int | None = None,
) -> TextInputDict:
    """A text box in a `label`; `value` fills it in beforehand."""
    if min_length is not None:
        _within("min_length", min_length, 0, MAX_INPUT)
    if max_length is not None:
        _within("max_length", max_length, 1, MAX_INPUT)
    text_input: TextInputDict = {
        "type": ComponentType.TEXT_INPUT,
        "custom_id": _checked(custom_id),
        "style": style,
        "required": required,
    }
    return _with(
        text_input,
        min_length=min_length,
        max_length=max_length,
        value=_short("value", value, MAX_INPUT),
        placeholder=_short("placeholder", placeholder, MAX_INPUT_PLACEHOLDER),
        id=id,
    )


def file_upload(
    custom_id: str,
    *,
    min_values: int = 1,
    max_values: int = 1,
    required: bool = True,
    file_types: Iterable[str] = (),
    id: int | None = None,
) -> FileUploadDict:
    """Up to ten files uploaded in a `label`; `file_types` such as "image" or ".pdf"."""
    _check_values(min_values, max_values, MAX_UPLOADS, required=required)
    types = list(file_types)
    _count("file_types", types, 0, MAX_FILE_TYPES)
    upload: FileUploadDict = {
        "type": ComponentType.FILE_UPLOAD,
        "custom_id": _checked(custom_id),
        "min_values": min_values,
        "max_values": max_values,
        "required": required,
    }
    return _with(upload, file_types=types or None, id=id)


def radio_group(
    custom_id: str,
    options: Iterable[GroupOptionDict],
    *,
    required: bool = True,
    id: int | None = None,
) -> RadioGroupDict:
    """Two to ten options in a `label`, of which one is picked."""
    listed = list(options)
    _count("a radio group", listed, 2, MAX_GROUP_OPTIONS)
    group: RadioGroupDict = {
        "type": ComponentType.RADIO_GROUP,
        "custom_id": _checked(custom_id),
        "options": listed,
        "required": required,
    }
    return _with(group, id=id)


def checkbox_group(
    custom_id: str,
    options: Iterable[GroupOptionDict],
    *,
    min_values: int = 1,
    max_values: int | None = None,
    required: bool = True,
    id: int | None = None,
) -> CheckboxGroupDict:
    """Checkboxes in a `label`; without `max_values` all of them can be ticked."""
    listed = list(options)
    _count("a checkbox group", listed, 1, MAX_GROUP_OPTIONS)
    _check_values(
        min_values,
        len(listed) if max_values is None else max_values,
        MAX_GROUP_OPTIONS,
        required=required,
    )
    group: CheckboxGroupDict = {
        "type": ComponentType.CHECKBOX_GROUP,
        "custom_id": _checked(custom_id),
        "options": listed,
        "min_values": min_values,
        "required": required,
    }
    return _with(group, max_values=max_values, id=id)


def group_option(
    label: str, value: str, *, description: str | None = None, default: bool = False
) -> GroupOptionDict:
    """One entry of a `radio_group` or `checkbox_group`."""
    option: GroupOptionDict = {
        "value": _short("value", value, MAX_OPTION_TEXT),
        "label": _short("label", label, MAX_OPTION_TEXT),
        "default": default,
    }
    return _with(
        option, description=_short("description", description, MAX_OPTION_TEXT)
    )


def checkbox(
    custom_id: str, *, default: bool = False, id: int | None = None
) -> CheckboxDict:
    """A single yes or no box in a `label`."""
    box: CheckboxDict = {
        "type": ComponentType.CHECKBOX,
        "custom_id": _checked(custom_id),
        "default": default,
    }
    return _with(box, id=id)


def _entity_select(
    type: _EntitySelectType,
    custom_id: str,
    defaults: list[SelectDefaultDict],
    *,
    placeholder: str | None,
    min_values: int,
    max_values: int,
    required: bool,
    disabled: bool,
    id: int | None,
) -> EntitySelectDict:
    _check_values(min_values, max_values, MAX_SELECT_OPTIONS, required=required)
    if defaults and not min_values <= len(defaults) <= max_values:
        raise ValueError(
            f"{len(defaults)} defaults; the select takes {min_values}-{max_values}"
        )
    select: EntitySelectDict = {
        "type": type,
        "custom_id": _checked(custom_id),
        "default_values": defaults,
        "min_values": min_values,
        "max_values": max_values,
        "disabled": disabled,
    }
    if not required:
        select["required"] = required
    return _with(
        select,
        placeholder=_short("placeholder", placeholder, MAX_PLACEHOLDER),
        id=id,
    )


def _defaults(ids: Iterable[int], type: SelectDefaultType) -> list[SelectDefaultDict]:
    return [{"id": id, "type": type} for id in ids]


def _labelled(
    button: ButtonDict, label: str | None, emoji: _Emoji | None, id: int | None
) -> ButtonDict:
    return _with(
        button,
        label=_short("label", label, MAX_BUTTON_LABEL),
        emoji=None if emoji is None else _emoji(emoji),
        id=id,
    )


def _with[C: Mapping[str, Any]](component: C, **fields: object) -> C:
    cast("dict[str, object]", component).update(
        (name, value) for name, value in fields.items() if value is not None
    )
    return component


def _emoji(emoji: _Emoji) -> Mapping[str, Any]:
    return {"name": emoji} if isinstance(emoji, str) else emoji


def _checked(custom_id: str) -> str:
    if not 1 <= len(custom_id) <= MAX_CUSTOM_ID:
        raise ValueError(
            f"custom_id must be 1-100 characters, got {len(custom_id)}: {custom_id!r}"
        )
    return custom_id


def _short[T: str | None](name: str, text: T, limit: int) -> T:
    if text is not None and len(text) > limit:
        raise ValueError(f"{name} is {len(text)} characters; Discord allows {limit}")
    return text


def _within(name: str, value: int, low: int, high: int) -> None:
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low}-{high}, got {value}")


def _count(what: str, items: Sequence[object], low: int, high: int) -> None:
    if not low <= len(items) <= high:
        raise ValueError(f"{what} takes {low}-{high} items, got {len(items)}")


def _check_values(
    min_values: int, max_values: int, high: int, *, required: bool
) -> None:
    if required and min_values == 0:
        raise ValueError("min_values=0 needs required=False")
    _within("min_values", min_values, 0, high)
    _within("max_values", max_values, max(1, min_values), high)
