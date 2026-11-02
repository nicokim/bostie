from collections.abc import Iterable
from typing import Literal, cast

import msgspec

from bostie.enums import (
    ButtonStyle,
    ChannelType,
    ComponentType,
    ContentScanMetadataFlags,
    UnfurledMediaItemFlags,
)
from bostie.models.base import Builder, Model, decoder


class PartialEmoji(Builder, kw_only=True):
    """A custom emoji by id, or a unicode emoji by name."""

    id: int | None = None
    name: str | None = None
    animated: bool | None = None


class _Component(Builder, kw_only=True, tag_field="type"):
    id: int | None = None

    @property
    def type(self) -> ComponentType:
        return ComponentType(cast("int", type(self).__struct_config__.tag))


class ActionRow(_Component, kw_only=True, tag=ComponentType.ACTION_ROW.value):
    _components: tuple[msgspec.Raw, ...] = msgspec.field(name="components")

    @property
    def components(self) -> "tuple[Component, ...]":
        return decode_components(self._components)


class Button(_Component, kw_only=True, tag=ComponentType.BUTTON.value):
    style: ButtonStyle
    label: str | None = None
    emoji: PartialEmoji | None = None
    custom_id: str | None = None
    sku_id: int | None = None
    url: str | None = None
    disabled: bool | None = None


class SelectOption(Builder, kw_only=True):
    label: str
    value: str
    description: str | None = None
    emoji: PartialEmoji | None = None
    default: bool | None = None


type SelectDefaultType = Literal["user", "role", "channel"]


class SelectDefaultValue(Builder, kw_only=True):
    """A user, role or channel a select shows as already chosen."""

    id: int
    type: SelectDefaultType


class _Select(_Component, kw_only=True):
    custom_id: str
    placeholder: str | None = None
    min_values: int | None = None
    max_values: int | None = None
    disabled: bool | None = None


class StringSelect(_Select, kw_only=True, tag=ComponentType.STRING_SELECT.value):
    options: tuple[SelectOption, ...]


class _EntitySelect(_Select, kw_only=True):
    default_values: tuple[SelectDefaultValue, ...] = ()


class UserSelect(_EntitySelect, kw_only=True, tag=ComponentType.USER_SELECT.value):
    pass


class RoleSelect(_EntitySelect, kw_only=True, tag=ComponentType.ROLE_SELECT.value):
    pass


class MentionableSelect(
    _EntitySelect, kw_only=True, tag=ComponentType.MENTIONABLE_SELECT.value
):
    pass


class ChannelSelect(
    _EntitySelect, kw_only=True, tag=ComponentType.CHANNEL_SELECT.value
):
    channel_types: tuple[ChannelType, ...] = ()


class ContentScanMetadata(Builder, kw_only=True):
    """What Discord's content scan found in a media item."""

    flags: ContentScanMetadataFlags


class UnfurledMediaItem(Builder, kw_only=True):
    """An image, video or file shown by a component; only `url` is set when sending."""

    url: str
    proxy_url: str | None = None
    height: int | None = None
    width: int | None = None
    placeholder: str | None = None
    placeholder_version: int | None = None
    content_type: str | None = None
    flags: UnfurledMediaItemFlags | None = None
    content_scan_metadata: ContentScanMetadata | None = None
    attachment_id: int | None = None


class Section(_Component, kw_only=True, tag=ComponentType.SECTION.value):
    """Text components with a button or thumbnail beside them."""

    _components: tuple[msgspec.Raw, ...] = msgspec.field(name="components")
    _accessory: msgspec.Raw = msgspec.field(name="accessory")

    @property
    def components(self) -> "tuple[Component, ...]":
        return decode_components(self._components)

    @property
    def accessory(self) -> "Component":
        return _decode_component(self._accessory)


class TextDisplay(_Component, kw_only=True, tag=ComponentType.TEXT_DISPLAY.value):
    content: str


class Thumbnail(_Component, kw_only=True, tag=ComponentType.THUMBNAIL.value):
    media: UnfurledMediaItem
    description: str | None = None
    spoiler: bool | None = None


class MediaGalleryItem(Builder, kw_only=True):
    media: UnfurledMediaItem
    description: str | None = None
    spoiler: bool | None = None


class MediaGallery(_Component, kw_only=True, tag=ComponentType.MEDIA_GALLERY.value):
    items: tuple[MediaGalleryItem, ...]


class FileComponent(_Component, kw_only=True, tag=ComponentType.FILE.value):
    """A file attached to the message, shown in its place among the components."""

    file: UnfurledMediaItem
    spoiler: bool | None = None
    name: str | None = None
    size: int | None = None


class Separator(_Component, kw_only=True, tag=ComponentType.SEPARATOR.value):
    divider: bool | None = None
    spacing: int | None = None


class Container(_Component, kw_only=True, tag=ComponentType.CONTAINER.value):
    _components: tuple[msgspec.Raw, ...] = msgspec.field(name="components")
    accent_color: int | None = None
    spoiler: bool | None = None

    @property
    def components(self) -> "tuple[Component, ...]":
        return decode_components(self._components)


class UnknownComponent(msgspec.Struct, kw_only=True, frozen=True, gc=False):
    """A component bostie cannot model; `raw` is sent back unchanged."""

    type: int | None
    raw: msgspec.Raw


type KnownComponent = (
    ActionRow
    | Button
    | StringSelect
    | UserSelect
    | RoleSelect
    | MentionableSelect
    | ChannelSelect
    | Section
    | TextDisplay
    | Thumbnail
    | MediaGallery
    | FileComponent
    | Separator
    | Container
)
type Component = KnownComponent | UnknownComponent


class _Submitted(Model, kw_only=True, tag_field="type"):
    id: int


class SubmittedTextDisplay(
    _Submitted, kw_only=True, tag=ComponentType.TEXT_DISPLAY.value
):
    """A text display of a submitted modal, which carries no answer."""


class SubmittedLabel(_Submitted, kw_only=True, tag=ComponentType.LABEL.value):
    """A label of a submitted modal and the answered component inside it."""

    _component: msgspec.Raw = msgspec.field(name="component")

    @property
    def component(self) -> "SubmittedComponent":
        return _decode_submitted(self._component)


class SubmittedInput(_Submitted, kw_only=True):
    """What the user answered in one component of a modal."""

    custom_id: str


class SubmittedTextInput(
    SubmittedInput, kw_only=True, tag=ComponentType.TEXT_INPUT.value
):
    value: str


class SubmittedValues(SubmittedInput, kw_only=True):
    """An answer of several values; those of entity selects and uploads are ids."""

    values: tuple[str, ...]


class SubmittedStringSelect(
    SubmittedValues, kw_only=True, tag=ComponentType.STRING_SELECT.value
):
    pass


class SubmittedUserSelect(
    SubmittedValues, kw_only=True, tag=ComponentType.USER_SELECT.value
):
    pass


class SubmittedRoleSelect(
    SubmittedValues, kw_only=True, tag=ComponentType.ROLE_SELECT.value
):
    pass


class SubmittedMentionableSelect(
    SubmittedValues, kw_only=True, tag=ComponentType.MENTIONABLE_SELECT.value
):
    pass


class SubmittedChannelSelect(
    SubmittedValues, kw_only=True, tag=ComponentType.CHANNEL_SELECT.value
):
    pass


class SubmittedFileUpload(
    SubmittedValues, kw_only=True, tag=ComponentType.FILE_UPLOAD.value
):
    pass


class SubmittedRadioGroup(
    SubmittedInput, kw_only=True, tag=ComponentType.RADIO_GROUP.value
):
    value: str | None


class SubmittedCheckboxGroup(
    SubmittedValues, kw_only=True, tag=ComponentType.CHECKBOX_GROUP.value
):
    pass


class SubmittedCheckbox(SubmittedInput, kw_only=True, tag=ComponentType.CHECKBOX.value):
    value: bool


type KnownSubmitted = (
    SubmittedTextDisplay
    | SubmittedLabel
    | SubmittedTextInput
    | SubmittedStringSelect
    | SubmittedUserSelect
    | SubmittedRoleSelect
    | SubmittedMentionableSelect
    | SubmittedChannelSelect
    | SubmittedFileUpload
    | SubmittedRadioGroup
    | SubmittedCheckboxGroup
    | SubmittedCheckbox
)
type SubmittedComponent = KnownSubmitted | UnknownComponent


class _ComponentTag(msgspec.Struct):
    type: int | None = None


def decode_components(raws: Iterable[msgspec.Raw]) -> tuple[Component, ...]:
    """Decode components; those of unknown type or shape become `UnknownComponent`."""
    return tuple(_decode_component(raw) for raw in raws)


def decode_submitted(raws: Iterable[msgspec.Raw]) -> tuple[SubmittedComponent, ...]:
    """Decode a modal's answered components; unknown ones become `UnknownComponent`."""
    return tuple(_decode_submitted(raw) for raw in raws)


def _decode_component(raw: msgspec.Raw) -> Component:
    try:
        component: KnownComponent = decoder(KnownComponent.__value__).decode(raw)
    except msgspec.ValidationError:
        return UnknownComponent(type=_component_type(raw), raw=raw)
    return component


def _decode_submitted(raw: msgspec.Raw) -> SubmittedComponent:
    try:
        component: KnownSubmitted = decoder(KnownSubmitted.__value__).decode(raw)
    except msgspec.ValidationError:
        return UnknownComponent(type=_component_type(raw), raw=raw)
    return component


def _component_type(raw: msgspec.Raw) -> int | None:
    try:
        tag: int | None = decoder(_ComponentTag).decode(raw).type
    except msgspec.ValidationError:
        return None
    return tag
