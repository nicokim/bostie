from functools import cache
from typing import Any, dataclass_transform

import msgspec


@dataclass_transform(
    field_specifiers=(msgspec.field,), kw_only_default=True, frozen_default=True
)
class Model(msgspec.Struct, kw_only=True, frozen=True, gc=False):
    """Base of the models lyrecord decodes from Discord payloads."""

    raw_json: bytes | None = msgspec.field(default=None, name="__lyrecord_raw__")

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
    """The shared lenient JSON decoder for a type."""
    return msgspec.json.Decoder(type_, strict=False)


def decode[M: Model](model: type[M], data: bytes) -> M:
    """Decode a Discord payload into a model that keeps the payload as its raw JSON."""
    value: M = decoder(model).decode(data)
    msgspec.structs.force_setattr(value, "raw_json", data)
    return value
