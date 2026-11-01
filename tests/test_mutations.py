import logging
import re
from collections.abc import Callable, Iterator
from enum import IntFlag
from functools import partial
from pathlib import Path
from typing import Any

import msgspec
import pytest

from bostie import events
from bostie.enums import DiscordIntEnum, Permissions
from bostie.events import (
    EVENTS,
    GuildCreate,
    MessageCreate,
    decode_event,
)
from bostie.models import (
    ActionRow,
    Channel,
    CommandInteraction,
    ComponentInteraction,
    Guild,
    Member,
    Message,
    Model,
    PartialMessage,
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
}
ENUM_KEYS = {
    "type",
    "style",
    "component_type",
    "context",
    "verification_level",
    "premium_tier",
    "nsfw_level",
    "format_type",
}
FLAG_KEYS = {
    "flags",
    "flags_new",
    "permissions",
    "app_permissions",
    "allow",
    "deny",
    "public_flags",
}
UNKNOWN_FLAGS = 1 << 62 | 1
TOKEN = "bostie-test-token-7c1f"
MISSING = re.compile(
    r"Object missing required field `(?P<key>[^`]+)`(?: - at `\$(?P<at>.*)`)?$"
)
NULL = re.compile(r"got `null`(?: - at `\$(?P<at>.*)`)?$")
SNOWFLAKE_KEY = re.compile(r"\.\d+(?=\.|\[|$)")
SHAPE_KEYS = {("GUILD_CREATE.unavailable", "$.unavailable")}

type Decoder = Callable[[bytes], object]


def subclasses(base: type) -> Iterator[type]:
    subclass: type
    for subclass in base.__subclasses__():
        yield subclass
        yield from subclasses(subclass)


SECRET_KEYS = {
    field.encode_name
    for struct in (*subclasses(Model), *subclasses(events.Event))
    for field in msgspec.structs.fields(struct)
    if "token" in field.encode_name or "secret" in field.encode_name
}


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


def fields(value: Any, path: str = "$") -> Iterator[tuple[str, dict[str, Any], str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}.{key}"
            if not is_snowflake_map(value):
                yield where, value, key
            yield from fields(item, where)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from fields(item, f"{path}[{index}]")


def walk(value: object) -> Iterator[object]:
    yield value
    if isinstance(value, PartialMessage | ActionRow):
        yield from walk(value.components)
    if isinstance(value, PartialMessage):
        yield from walk(value.referenced_message)
    if isinstance(value, ComponentInteraction):
        yield from walk(value.message)
    if isinstance(value, GuildCreate):
        yield from walk(value.members)
    if isinstance(value, msgspec.Struct):
        for name in value.__struct_fields__:
            yield from walk(getattr(value, name.lstrip("_")))
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
    collections: bool,
) -> list[str]:
    document = load(path)
    failed = []
    for where, container, key in list(fields(document)):
        original = container[key]
        collection = isinstance(original, list) or (
            isinstance(original, dict) and is_snowflake_map(original)
        )
        if (collection and not collections) or (path.stem, where) in SHAPE_KEYS:
            continue
        mutate(container, key)
        try:
            decode_all(decoder, document)
        except msgspec.ValidationError as error:
            if not refused_by_its_model(error, where):
                failed.append(f"{where}: {error}")
        except Exception as error:
            failed.append(f"{where}: {error!r}")
        container[key] = original
    return failed


def refused_by_its_model(error: msgspec.ValidationError, where: str) -> bool:
    where = SNOWFLAKE_KEY.sub("[...]", where)
    if missing := MISSING.match(str(error)):
        return where.endswith(f"{missing['at'] or ''}.{missing['key']}")
    if null := NULL.search(str(error)):
        return where.endswith(null["at"] or "message")
    return False


def set_null(container: dict[str, Any], key: str) -> None:
    container[key] = None


def delete(container: dict[str, Any], key: str) -> None:
    del container[key]


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_unknown_keys_at_every_level_are_ignored(path: Path, decoder: Decoder) -> None:
    document = load(path)
    for value in list(objects(document)):
        value["bostie_future_field"] = {"nested": [1, {"deeper": None}]}

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
    assert all(value in (0, UNKNOWN_FLAGS) for value in flags)


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_unknown_component_types_become_unknown_components(
    path: Path, decoder: Decoder
) -> None:
    document = load(path)
    for component in components(document):
        component["type"] = 99

    decoded = decode_all(decoder, document)

    for message in [value for value in decoded if isinstance(value, PartialMessage)]:
        assert all(
            isinstance(component, UnknownComponent) and component.type == 99
            for component in message.components
        )


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_no_repr_shows_a_token_or_the_payload_holding_it(
    path: Path, decoder: Decoder
) -> None:
    document = load(path)
    for value in list(objects(document)):
        for key in SECRET_KEYS & value.keys():
            value[key] = TOKEN

    for decoded in decode_all(decoder, document):
        if isinstance(decoded, msgspec.Struct):
            for text in (repr(decoded), str(decoded), repr(decoded.__rich_repr__())):
                assert TOKEN not in text


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
def test_null_is_accepted_wherever_the_model_allows_it(
    path: Path, decoder: Decoder
) -> None:
    assert failures(path, decoder, set_null, collections=False) == []


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_a_field_may_be_missing_wherever_the_model_allows_it(
    path: Path, decoder: Decoder
) -> None:
    assert failures(path, decoder, delete, collections=True) == []


def test_a_dispatch_with_a_changed_field_type_is_skipped_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(events, "_undecodable", set())
    document = load(FIXTURES / "events" / "MESSAGE_CREATE.json")
    valid = msgspec.json.encode(document)
    document["mentions"] = "everyone"
    changed = msgspec.json.encode(document)

    with caplog.at_level(logging.DEBUG, logger="bostie.events"):
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


def test_permissions_beyond_64_bits_decode() -> None:
    bit = 1 << 70
    guild = load(FIXTURES / "events" / "GUILD_CREATE.json")
    guild["roles"][3]["permissions"] = str(bit | Permissions.ADMINISTRATOR)
    overwrite = guild["channels"][3]["permission_overwrites"][0]
    overwrite["allow"] = str(bit | Permissions.VIEW_CHANNEL)
    overwrite["deny"] = str(bit)
    interaction = load(FIXTURES / "events" / "INTERACTION_CREATE.command.json")
    interaction["app_permissions"] = str(bit)
    resolved_channels = interaction["data"]["resolved"]["channels"]
    channel_id, channel = next(iter(resolved_channels.items()))
    channel["permissions"] = str(bit | Permissions.SEND_MESSAGES)

    created = decode_event(GuildCreate, 0, msgspec.json.encode(guild))
    received = decode(CommandInteraction, msgspec.json.encode(interaction))

    assert created is not None
    role_permissions = created.roles[3].permissions
    assert role_permissions & bit
    assert Permissions.ADMINISTRATOR in role_permissions
    decoded_overwrite = created.channels[3].permission_overwrites[0]
    assert decoded_overwrite.allow == bit | Permissions.VIEW_CHANNEL
    assert decoded_overwrite.deny == bit
    assert received.app_permissions == bit
    resolved_channel = received.data.resolved.channels[int(channel_id)]
    assert resolved_channel.permissions == bit | Permissions.SEND_MESSAGES
