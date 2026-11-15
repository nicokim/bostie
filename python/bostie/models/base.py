from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import cache
from typing import Any, Protocol, cast, dataclass_transform, overload

import msgspec

from bostie.enums import Permissions
from bostie.snowflake import snowflake_time

HIDDEN_FIELDS = frozenset(
    {"raw_json", "token", "access_token", "refresh_token", "id_token"}
)


def shown_fields(struct: msgspec.Struct) -> list[tuple[str, Any]]:
    return [
        (name, getattr(struct, name))
        for name in struct.__struct_fields__
        if name not in HIDDEN_FIELDS and not name.startswith("_")
    ]


def redacted_repr(self: msgspec.Struct) -> str:
    shown = ", ".join(f"{name}={value!r}" for name, value in shown_fields(self))
    return f"{type(self).__name__}({shown})"


class _Identified(Protocol):
    @property
    def id(self) -> int: ...


class _MaybeIdentified(Protocol):
    @property
    def id(self) -> int | None: ...


class Snowflake:
    """A model whose id is a snowflake, the moment it was created in its bits."""

    __slots__ = ()

    @property
    def created_at(self: _Identified) -> datetime:
        """When the object was created, in UTC, read from its id."""
        return snowflake_time(self.id)


class MaybeSnowflake:
    """A model whose id is a snowflake when it has one."""

    __slots__ = ()

    @property
    def created_at(self: _MaybeIdentified) -> datetime | None:
        """When the object was created, in UTC, or None without an id."""
        return None if self.id is None else snowflake_time(self.id)


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

    __repr__ = redacted_repr
    __rich_repr__ = shown_fields


@overload
def decoder[T: msgspec.Struct](type_: type[T]) -> msgspec.json.Decoder[T]: ...


@overload
def decoder(type_: Any) -> msgspec.json.Decoder[Any]: ...


@cache
def decoder(type_: Any) -> msgspec.json.Decoder[Any]:
    """The shared lax JSON decoder for a type.

    Lax mode turns snowflake strings into int and unix numbers into datetime.
    """
    return msgspec.json.Decoder(type_, strict=False)


def _as_builtin(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(cast("Mapping[object, object]", value))
    if isinstance(value, Sequence):
        return list(cast("Sequence[object]", value))
    raise TypeError(f"Encoding objects of type {type(value).__name__} is unsupported")


encoder = msgspec.json.Encoder(enc_hook=_as_builtin)


def decode[M: Model](model: type[M], data: bytes) -> M:
    """Decode a Discord payload into a model that keeps the payload as its raw JSON."""
    value = decoder(model).decode(data)
    msgspec.structs.force_setattr(value, "raw_json", data)
    return value


def check_keys(given: Mapping[str, object], allowed: frozenset[str], what: str) -> None:
    unknown = given.keys() - allowed
    if unknown:
        raise TypeError(f"unexpected {what}: {', '.join(sorted(unknown))}")


def permissions_from(wire: str | None) -> Permissions | None:
    return None if wire is None else Permissions(int(wire))
