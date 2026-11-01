from collections.abc import Iterable, Mapping
from typing import Any, Literal, NewType, NotRequired, TypedDict

from bostie.custom_id import MAX_CUSTOM_ID
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
    style: ButtonStyle
    label: NotRequired[str]
    emoji: NotRequired[Mapping[str, Any]]
    custom_id: NotRequired[str]
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
    components: list[_RowChild]


class TextDict(TypedDict):
    type: Literal[ComponentType.TEXT_DISPLAY]
    content: str


class ThumbnailDict(TypedDict):
    type: Literal[ComponentType.THUMBNAIL]
    media: dict[str, str]
    description: NotRequired[str]
    spoiler: bool


class SectionDict(TypedDict):
    type: Literal[ComponentType.SECTION]
    components: list[TextDict | RawComponent]
    accessory: ButtonDict | ThumbnailDict | RawComponent


class GalleryItemDict(TypedDict):
    media: dict[str, str]
    description: NotRequired[str]
    spoiler: bool


class MediaGalleryDict(TypedDict):
    type: Literal[ComponentType.MEDIA_GALLERY]
    items: list[GalleryItemDict | RawComponent]


class FileDict(TypedDict):
    type: Literal[ComponentType.FILE]
    file: dict[str, str]
    spoiler: bool


class SeparatorDict(TypedDict):
    type: Literal[ComponentType.SEPARATOR]
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
    components: list[_ContainerChild]
    accent_color: NotRequired[int]
    spoiler: bool


class TextInputDict(TypedDict):
    type: Literal[ComponentType.TEXT_INPUT]
    custom_id: str
    style: TextInputStyle
    min_length: NotRequired[int]
    max_length: NotRequired[int]
    required: bool
    value: NotRequired[str]
    placeholder: NotRequired[str]


class FileUploadDict(TypedDict):
    type: Literal[ComponentType.FILE_UPLOAD]
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
    custom_id: str
    options: list[GroupOptionDict]
    required: bool


class CheckboxGroupDict(TypedDict):
    type: Literal[ComponentType.CHECKBOX_GROUP]
    custom_id: str
    options: list[GroupOptionDict]
    min_values: int
    max_values: NotRequired[int]
    required: bool


class CheckboxDict(TypedDict):
    type: Literal[ComponentType.CHECKBOX]
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


def row(*components: _RowChild) -> RowDict:
    """An action row holding up to five buttons or one select menu."""
    return {"type": ComponentType.ACTION_ROW, "components": list(components)}


def button(
    label: str | None,
    *,
    custom_id: str,
    style: InteractiveStyle = ButtonStyle.SECONDARY,
    emoji: _Emoji | None = None,
    disabled: bool = False,
) -> ButtonDict:
    """A button that sends `custom_id` to the bot when clicked."""
    button: ButtonDict = {
        "type": ComponentType.BUTTON,
        "style": style,
        "custom_id": _checked(custom_id),
        "disabled": disabled,
    }
    return _labelled(button, label, emoji)


def link_button(
    label: str | None, url: str, *, emoji: _Emoji | None = None, disabled: bool = False
) -> ButtonDict:
    """A button that opens `url` and never reaches the bot."""
    button: ButtonDict = {
        "type": ComponentType.BUTTON,
        "style": ButtonStyle.LINK,
        "url": url,
        "disabled": disabled,
    }
    return _labelled(button, label, emoji)


def string_select(
    custom_id: str,
    options: Iterable[SelectOptionDict],
    *,
    placeholder: str | None = None,
    min_values: int = 1,
    max_values: int = 1,
    required: bool = True,
    disabled: bool = False,
) -> StringSelectDict:
    """A select menu of fixed options; build them with `select_option`."""
    select: StringSelectDict = {
        "type": ComponentType.STRING_SELECT,
        "custom_id": _checked(custom_id),
        "options": list(options),
        "min_values": min_values,
        "max_values": max_values,
        "disabled": disabled,
    }
    if placeholder is not None:
        select["placeholder"] = placeholder
    if not required:
        select["required"] = required
    return select


def user_select(
    custom_id: str,
    *,
    defaults: Iterable[int] = (),
    placeholder: str | None = None,
    min_values: int = 1,
    max_values: int = 1,
    required: bool = True,
    disabled: bool = False,
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
    option: SelectOptionDict = {"label": label, "value": value, "default": default}
    if description is not None:
        option["description"] = description
    if emoji is not None:
        option["emoji"] = _emoji(emoji)
    return option


def text(content: str) -> TextDict:
    """Markdown text; Components V2 messages use it instead of `content`."""
    return {"type": ComponentType.TEXT_DISPLAY, "content": content}


def section(
    *texts: TextDict | RawComponent,
    accessory: ButtonDict | ThumbnailDict | RawComponent,
) -> SectionDict:
    """One to three `text` components with a `button` or `thumbnail` beside them."""
    return {
        "type": ComponentType.SECTION,
        "components": list(texts),
        "accessory": accessory,
    }


def thumbnail(
    url: str, *, description: str | None = None, spoiler: bool = False
) -> ThumbnailDict:
    """A small image for a `section`; `url` may be `attachment://<filename>`."""
    thumbnail: ThumbnailDict = {
        "type": ComponentType.THUMBNAIL,
        "media": {"url": url},
        "spoiler": spoiler,
    }
    if description is not None:
        thumbnail["description"] = description
    return thumbnail


def media_gallery(*items: GalleryItemDict | RawComponent) -> MediaGalleryDict:
    """One to ten images or videos built with `gallery_item`."""
    return {"type": ComponentType.MEDIA_GALLERY, "items": list(items)}


def gallery_item(
    url: str, *, description: str | None = None, spoiler: bool = False
) -> GalleryItemDict:
    """One entry of a `media_gallery`; `url` may be `attachment://<filename>`."""
    item: GalleryItemDict = {"media": {"url": url}, "spoiler": spoiler}
    if description is not None:
        item["description"] = description
    return item


def file(filename: str, *, spoiler: bool = False) -> FileDict:
    """A file uploaded with the message, named as in its `File`."""
    return {
        "type": ComponentType.FILE,
        "file": {"url": f"attachment://{filename}"},
        "spoiler": spoiler,
    }


def separator(*, divider: bool = True, spacing: Literal[1, 2] = 1) -> SeparatorDict:
    """Space between components; `spacing=2` is large, and `divider` draws a line."""
    return {"type": ComponentType.SEPARATOR, "divider": divider, "spacing": spacing}


def container(
    *components: _ContainerChild,
    accent_color: int | None = None,
    spoiler: bool = False,
) -> ContainerDict:
    """A box around rows, texts, sections, galleries, files and separators."""
    container: ContainerDict = {
        "type": ComponentType.CONTAINER,
        "components": list(components),
        "spoiler": spoiler,
    }
    if accent_color is not None:
        container["accent_color"] = accent_color
    return container


def modal(custom_id: str, title: str, *components: _ModalChild) -> ModalDict:
    """A popup form of one to five `label` and `text` components."""
    return {
        "custom_id": _checked(custom_id),
        "title": title,
        "components": list(components),
    }


def label(
    label: str, component: _LabelChild, *, description: str | None = None
) -> LabelDict:
    """A modal field: `label` and `description` over one input, select or upload."""
    field: LabelDict = {
        "type": ComponentType.LABEL,
        "label": label,
        "component": component,
    }
    if description is not None:
        field["description"] = description
    return field


def text_input(
    custom_id: str,
    *,
    style: TextInputStyle = TextInputStyle.SHORT,
    placeholder: str | None = None,
    value: str | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    required: bool = True,
) -> TextInputDict:
    """A text box in a `label`; `value` fills it in beforehand."""
    text_input: TextInputDict = {
        "type": ComponentType.TEXT_INPUT,
        "custom_id": _checked(custom_id),
        "style": style,
        "required": required,
    }
    if min_length is not None:
        text_input["min_length"] = min_length
    if max_length is not None:
        text_input["max_length"] = max_length
    if value is not None:
        text_input["value"] = value
    if placeholder is not None:
        text_input["placeholder"] = placeholder
    return text_input


def file_upload(
    custom_id: str,
    *,
    min_values: int = 1,
    max_values: int = 1,
    required: bool = True,
    file_types: Iterable[str] = (),
) -> FileUploadDict:
    """Up to ten files uploaded in a `label`; `file_types` such as "image" or ".pdf"."""
    upload: FileUploadDict = {
        "type": ComponentType.FILE_UPLOAD,
        "custom_id": _checked(custom_id),
        "min_values": min_values,
        "max_values": max_values,
        "required": required,
    }
    types = list(file_types)
    if types:
        upload["file_types"] = types
    return upload


def radio_group(
    custom_id: str, options: Iterable[GroupOptionDict], *, required: bool = True
) -> RadioGroupDict:
    """Two to ten options in a `label`, of which one is picked."""
    return {
        "type": ComponentType.RADIO_GROUP,
        "custom_id": _checked(custom_id),
        "options": list(options),
        "required": required,
    }


def checkbox_group(
    custom_id: str,
    options: Iterable[GroupOptionDict],
    *,
    min_values: int = 1,
    max_values: int | None = None,
    required: bool = True,
) -> CheckboxGroupDict:
    """Checkboxes in a `label`; without `max_values` all of them can be ticked."""
    group: CheckboxGroupDict = {
        "type": ComponentType.CHECKBOX_GROUP,
        "custom_id": _checked(custom_id),
        "options": list(options),
        "min_values": min_values,
        "required": required,
    }
    if max_values is not None:
        group["max_values"] = max_values
    return group


def group_option(
    label: str, value: str, *, description: str | None = None, default: bool = False
) -> GroupOptionDict:
    """One entry of a `radio_group` or `checkbox_group`."""
    option: GroupOptionDict = {"value": value, "label": label, "default": default}
    if description is not None:
        option["description"] = description
    return option


def checkbox(custom_id: str, *, default: bool = False) -> CheckboxDict:
    """A single yes or no box in a `label`."""
    return {
        "type": ComponentType.CHECKBOX,
        "custom_id": _checked(custom_id),
        "default": default,
    }


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
) -> EntitySelectDict:
    select: EntitySelectDict = {
        "type": type,
        "custom_id": _checked(custom_id),
        "default_values": defaults,
        "min_values": min_values,
        "max_values": max_values,
        "disabled": disabled,
    }
    if placeholder is not None:
        select["placeholder"] = placeholder
    if not required:
        select["required"] = required
    return select


def _defaults(ids: Iterable[int], type: SelectDefaultType) -> list[SelectDefaultDict]:
    return [{"id": id, "type": type} for id in ids]


def _labelled(
    button: ButtonDict, label: str | None, emoji: _Emoji | None
) -> ButtonDict:
    if label is not None:
        button["label"] = label
    if emoji is not None:
        button["emoji"] = _emoji(emoji)
    return button


def _emoji(emoji: _Emoji) -> Mapping[str, Any]:
    return {"name": emoji} if isinstance(emoji, str) else emoji


def _checked(custom_id: str) -> str:
    if not 1 <= len(custom_id) <= MAX_CUSTOM_ID:
        raise ValueError(
            f"custom_id must be 1-100 characters, got {len(custom_id)}: {custom_id!r}"
        )
    return custom_id
