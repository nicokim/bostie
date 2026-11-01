from collections.abc import Mapping, Sequence
from functools import cache
from typing import Any, dataclass_transform

import msgspec

from bostie.enums import Permissions

HIDDEN_FIELDS = frozenset({"raw_json", "token"})


def shown_fields(struct: msgspec.Struct) -> list[tuple[str, Any]]:
    return [
        (name, getattr(struct, name))
        for name in struct.__struct_fields__
        if name not in HIDDEN_FIELDS
    ]


def redacted_repr(self: msgspec.Struct) -> str:
    shown = ", ".join(f"{name}={value!r}" for name, value in shown_fields(self))
    return f"{type(self).__name__}({shown})"


@dataclass_transform(
    field_specifiers=(msgspec.field,), kw_only_default=True, frozen_default=True
)
class Model(msgspec.Struct, kw_only=True, frozen=True, gc=False):
    """Base of the models bostie decodes from Discord payloads."""

    raw_json: bytes | None = msgspec.field(default=None, name="__bostie_raw__")

    __repr__ = redacted_repr
    __rich_repr__ = shown_fields

    @property
    def raw(self) -> Any:
        """The payload this object was received as, or None for nested objects."""
        return None if self.raw_json is None else msgspec.json.decode(self.raw_json)


@dataclass_transform(
    field_specifiers=(msgspec.field,), kw_only_default=True, frozen_default=True
)
class Builder(msgspec.Struct, kw_only=True, frozen=True, gc=False, omit_defaults=True):
    """Base of the models that are decoded from Discord and can be sent back to it."""


@cache
def decoder(type_: Any) -> msgspec.json.Decoder[Any]:
    """The shared lax JSON decoder for a type.

    Lax mode turns snowflake strings into int and unix numbers into datetime.
    """
    return msgspec.json.Decoder(type_, strict=False)


def _as_builtin(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, Sequence):
        return list(value)
    raise TypeError(f"Encoding objects of type {type(value).__name__} is unsupported")


encoder = msgspec.json.Encoder(enc_hook=_as_builtin)


def decode[M: Model](model: type[M], data: bytes) -> M:
    """Decode a Discord payload into a model that keeps the payload as its raw JSON."""
    value: M = decoder(model).decode(data)
    msgspec.structs.force_setattr(value, "raw_json", data)
    return value


def permissions_from(wire: str | None) -> Permissions | None:
    return None if wire is None else Permissions(int(wire))
