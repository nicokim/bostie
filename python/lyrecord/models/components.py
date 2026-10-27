from collections.abc import Iterable
from contextlib import suppress

import msgspec

from lyrecord.enums import ButtonStyle, ChannelType, ComponentType
from lyrecord.models.base import Builder, decoder


class PartialEmoji(Builder, kw_only=True):
    """A custom emoji by id, or a unicode emoji by name."""

    id: int | None = None
    name: str | None = None
    animated: bool | None = None


class ActionRow(Builder, kw_only=True):
    type: ComponentType
    id: int | None = None
    components_json: tuple[msgspec.Raw, ...] = msgspec.field(
        default=(), name="components"
    )

    @property
    def components(self) -> "tuple[Component, ...]":
        return decode_components(self.components_json)


class Button(Builder, kw_only=True):
    type: ComponentType
    id: int | None = None
    style: ButtonStyle | None = None
    label: str | None = None
    emoji: PartialEmoji | None = None
    custom_id: str | None = None
    sku_id: int | None = None
    url: str | None = None
    disabled: bool | None = None


class SelectOption(Builder, kw_only=True):
    label: str | None = None
    value: str | None = None
    description: str | None = None
    emoji: PartialEmoji | None = None
    default: bool | None = None


class SelectMenu(Builder, kw_only=True):
    """A string, user, role, mentionable or channel select, told apart by `type`."""

    type: ComponentType
    id: int | None = None
    custom_id: str | None = None
    options: tuple[SelectOption, ...] = ()
    channel_types: tuple[ChannelType, ...] = ()
    placeholder: str | None = None
    min_values: int | None = None
    max_values: int | None = None
    disabled: bool | None = None


class UnknownComponent(msgspec.Struct, kw_only=True, frozen=True, gc=False):
    """A component lyrecord cannot model; `raw` is sent back unchanged."""

    type: int | None
    raw: msgspec.Raw


type Component = ActionRow | Button | SelectMenu | UnknownComponent


class _ComponentTag(msgspec.Struct):
    type: int | None = None


COMPONENT_MODELS: dict[int, type[ActionRow | Button | SelectMenu]] = {
    ComponentType.ACTION_ROW: ActionRow,
    ComponentType.BUTTON: Button,
    ComponentType.STRING_SELECT: SelectMenu,
    ComponentType.USER_SELECT: SelectMenu,
    ComponentType.ROLE_SELECT: SelectMenu,
    ComponentType.MENTIONABLE_SELECT: SelectMenu,
    ComponentType.CHANNEL_SELECT: SelectMenu,
}


def decode_components(raws: Iterable[msgspec.Raw]) -> tuple[Component, ...]:
    """Decode components; those of unknown type or shape become `UnknownComponent`."""
    return tuple(_decode_component(raw) for raw in raws)


def _decode_component(raw: msgspec.Raw) -> Component:
    tag = _component_type(raw)
    model = None if tag is None else COMPONENT_MODELS.get(tag)
    if model is not None:
        with suppress(msgspec.ValidationError):
            component: Component = decoder(model).decode(raw)
            return component
    return UnknownComponent(type=tag, raw=raw)


def _component_type(raw: msgspec.Raw) -> int | None:
    try:
        tag: int | None = decoder(_ComponentTag).decode(raw).type
    except msgspec.ValidationError:
        return None
    return tag
