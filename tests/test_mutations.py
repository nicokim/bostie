import logging
from collections.abc import Callable, Iterator
from enum import IntFlag
from functools import partial
from pathlib import Path
from typing import Any

import msgspec
import pytest

from lyrecord import events
from lyrecord.enums import DiscordIntEnum
from lyrecord.events import EVENTS, GuildCreate, MessageCreate, decode_event
from lyrecord.models import (
    ActionRow,
    Application,
    Channel,
    Guild,
    Member,
    Message,
    Model,
    UnknownComponent,
    User,
    decode,
)

FIXTURES = Path(__file__).parent / "fixtures" / "models"
REST_MODELS: dict[str, type[Model]] = {
    "user": User,
    "member": Member,
    "channel": Channel,
    "guild": Guild,
    "message": Message,
    "application_me": Application,
}
ENUM_KEYS = {"type", "style", "component_type", "context"}
FLAG_KEYS = {
    "flags",
    "flags_new",
    "permissions",
    "app_permissions",
    "allow",
    "deny",
    "public_flags",
}
UNKNOWN_FLAGS = 1 << 62

type Decoder = Callable[[bytes], object]


def fixtures() -> Iterator[Any]:
    for path in sorted((FIXTURES / "events").glob("*.json")):
        event = EVENTS.get(path.name.split(".")[0])
        if event is not None:
            yield pytest.param(path, partial(event.decode, 0), id=path.stem)
    for name, model in REST_MODELS.items():
        yield pytest.param(
            FIXTURES / "rest" / f"{name}.json", partial(decode, model), id=name
        )


ALL_FIXTURES = list(fixtures())


def load(path: Path) -> Any:
    return msgspec.json.decode(path.read_bytes())


def is_snowflake_map(value: dict[str, Any]) -> bool:
    return bool(value) and all(key.isdigit() for key in value)


def objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if not is_snowflake_map(value):
            yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def components(value: Any, inside: bool = False) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if inside:
            yield value
        for key, item in value.items():
            yield from components(item, key == "components")
    elif isinstance(value, list):
        for item in value:
            yield from components(item, inside)


def fields(
    value: Any, path: str = "$", component: bool = False
) -> Iterator[tuple[str, dict[str, Any], str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}.{key}"
            is_union_tag = component and key == "type"
            skipped = is_union_tag or key == "id"
            if not (skipped or is_snowflake_map(value)):
                yield where, value, key
            yield from fields(item, where, key == "components")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from fields(item, f"{path}[{index}]", component)


def walk(value: object) -> Iterator[object]:
    yield value
    if isinstance(value, Message | ActionRow):
        yield from walk(value.components)
    if isinstance(value, GuildCreate):
        yield from walk(value.members)
    if isinstance(value, msgspec.Struct):
        for name in value.__struct_fields__:
            yield from walk(getattr(value, name))
    elif isinstance(value, tuple | list):
        for item in value:
            yield from walk(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk(item)


def decode_all(decoder: Decoder, document: Any) -> list[object]:
    return list(walk(decoder(msgspec.json.encode(document))))


def failures(
    path: Path,
    decoder: Decoder,
    mutate: Callable[[dict[str, Any], str], None],
    *,
    arrays: bool,
) -> list[str]:
    document = load(path)
    failed = []
    for where, container, key in list(fields(document)):
        original = container[key]
        if isinstance(original, list) and not arrays:
            continue
        mutate(container, key)
        try:
            decode_all(decoder, document)
        except Exception as error:
            failed.append(f"{where}: {error!r}")
        container[key] = original
    return failed


def set_null(container: dict[str, Any], key: str) -> None:
    container[key] = None


def delete(container: dict[str, Any], key: str) -> None:
    del container[key]


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_unknown_keys_at_every_level_are_ignored(path: Path, decoder: Decoder) -> None:
    document = load(path)
    for value in list(objects(document)):
        value["lyrecord_future_field"] = {"nested": [1, {"deeper": None}]}

    decode_all(decoder, document)


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_unknown_enum_values_and_flag_bits_are_kept(
    path: Path, decoder: Decoder
) -> None:
    document = load(path)
    for value in list(objects(document)):
        for key, item in value.items():
            if key in ENUM_KEYS and isinstance(item, int):
                value[key] = 9999
            elif key == "channel_types":
                value[key] = [9999]
            elif key in FLAG_KEYS:
                value[key] = (
                    str(UNKNOWN_FLAGS) if isinstance(item, str) else UNKNOWN_FLAGS
                )

    decoded = decode_all(decoder, document)

    enums = [value for value in decoded if isinstance(value, DiscordIntEnum)]
    flags = [value for value in decoded if isinstance(value, IntFlag)]
    assert all(value.name == "UNKNOWN_9999" for value in enums)
    assert all(value == UNKNOWN_FLAGS for value in flags)


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_unknown_component_types_become_unknown_components(
    path: Path, decoder: Decoder
) -> None:
    document = load(path)
    for component in components(document):
        component["type"] = 99

    decoded = decode_all(decoder, document)

    for message in [value for value in decoded if isinstance(value, Message)]:
        assert all(
            isinstance(component, UnknownComponent) and component.type == 99
            for component in message.components
        )


def test_unknown_components_keep_their_raw_json() -> None:
    document = load(FIXTURES / "events" / "MESSAGE_CREATE.rich.json")
    for component in components(document):
        component["type"] = 99

    decoded = decode(Message, msgspec.json.encode(document)).components

    unknown = [
        msgspec.json.decode(component.raw)
        for component in decoded
        if isinstance(component, UnknownComponent)
    ]
    assert unknown == document["components"]


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_null_is_accepted_in_every_field_except_ids_and_arrays(
    path: Path, decoder: Decoder
) -> None:
    assert failures(path, decoder, set_null, arrays=False) == []


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_every_field_except_ids_may_be_missing(path: Path, decoder: Decoder) -> None:
    assert failures(path, decoder, delete, arrays=True) == []


def test_a_dispatch_with_a_changed_field_type_is_skipped_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(events, "_undecodable", set())
    document = load(FIXTURES / "events" / "MESSAGE_CREATE.json")
    valid = msgspec.json.encode(document)
    document["mentions"] = "everyone"
    changed = msgspec.json.encode(document)

    with caplog.at_level(logging.DEBUG, logger="lyrecord.events"):
        skipped = [decode_event(MessageCreate, 0, changed) for _ in range(3)]
        delivered = decode_event(MessageCreate, 0, valid)

    assert skipped == [None, None, None]
    assert delivered is not None
    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "MESSAGE_CREATE" in warnings[0].getMessage()
    assert "$.mentions" in warnings[0].getMessage()
