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
    SKU,
    ActionRow,
    ActiveThreads,
    ActivityInstance,
    Application,
    ApplicationCommand,
    AuditLogPage,
    Ban,
    Channel,
    CommandInteraction,
    ComponentInteraction,
    CurrentUserGuild,
    Entitlement,
    Guild,
    GuildOnboarding,
    GuildPreview,
    GuildTemplate,
    GuildWidget,
    GuildWidgetSettings,
    IncidentsData,
    Invite,
    Member,
    Message,
    Model,
    PartialMessage,
    ScheduledEventUser,
    StickerPack,
    UnknownComponent,
    User,
    VanityURL,
    VoiceRegion,
    Webhook,
    WebhookMessage,
    WelcomeScreen,
    decode,
)
from bostie.models.base import decoder
from bostie.testing import payloads
from bostie.webhook_events import WEBHOOK_EVENTS

FIXTURES = payloads.FIXTURES
WEBHOOK_FIXTURES = Path(__file__).parent / "fixtures" / "webhook_events"
REST_MODELS: dict[str, Any] = {
    "user": User,
    "member": Member,
    "channel": Channel,
    "guild": Guild,
    "message": Message,
    "audit_log_page": AuditLogPage,
    "ban": Ban,
    "current_user_guild": CurrentUserGuild,
    "guild_onboarding": GuildOnboarding,
    "guild_preview": GuildPreview,
    "guild_template": GuildTemplate,
    "guild_widget": GuildWidget,
    "guild_widget_settings": GuildWidgetSettings,
    "incidents_data": IncidentsData,
    "scheduled_event_user": ScheduledEventUser,
    "vanity_url": VanityURL,
    "voice_region": VoiceRegion,
    "welcome_screen": WelcomeScreen,
    "poll_message": Message,
    "forum_channel": Channel,
    "active_threads": ActiveThreads,
    "webhook": Webhook,
    "webhook_follower": Webhook,
    "application_me": Application,
    "invite": Invite,
    "sku": SKU,
    "entitlement": Entitlement,
    "sticker_pack": StickerPack,
    "activity_instance": ActivityInstance,
    "commands": tuple[ApplicationCommand, ...],
    "interaction_callback": WebhookMessage,
}
PAYLOAD_AT = {"interaction_callback": ("resource", "message")}
UNMODELLED_KEYS = {
    f"{model}.{key}"
    for model, keys in {
        "Activity": "id",
        "AvatarDecorationData": "expires_at",
        "Channel": "newly_created",
        "CommandData": "version",
        "PartialEntitlement": "gift_code_flags promotion_id",
        "Entitlement": "subscription_id",
        "Guild": "activity_instances embedded_activities home_header hub_type "
        "inventory_settings latest_onboarding_question_id lazy nsfw version",
        "Nameplate": "expires_at",
        "PartialChannel": "recipients",
        "PartialUser": "avatar_decoration_data clan collectibles discriminator "
        "display_name_styles flags global_name primary_guild public_flags",
        "User": "banner_color clan",
        "Ready": "_trace auth game_relationships geo_ordered_rtc_regions "
        "guild_join_requests presences private_channels relationships "
        "session_type user_settings",
        "Resumed": "_trace",
        "Role": "description",
        "ScheduledEvent": "sku_ids",
        "SKU": "access_type dependent_sku_id features manifest_labels premium "
        "release_date show_age_gate",
        "TemplateGuild": "region",
        "TemplateRole": "color",
    }.items()
    for key in keys.split()
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
    "event_type",
    "trigger_type",
    "rule_trigger_type",
    "action_type",
    "privacy_level",
    "status",
    "entity_type",
    "frequency",
    "day",
    "status_display_type",
    "animation_type",
    "target_type",
    "expire_behavior",
    "channel_type",
    "mode",
    "layout_type",
    "video_quality_mode",
    "default_sort_order",
    "default_forum_layout",
    "mfa_level",
    "default_message_notifications",
    "explicit_content_filter",
    "premium_type",
    "event_webhooks_status",
    "integration_type",
    "handler",
}
ENUM_LIST_KEYS = {
    "channel_types",
    "presets",
    "by_weekday",
    "by_month",
    "integration_types",
    "contexts",
}
FLAG_KEYS = {
    "flags",
    "flags_new",
    "permissions",
    "app_permissions",
    "default_member_permissions",
    "allow",
    "deny",
    "public_flags",
    "system_channel_flags",
}
UNKNOWN_FLAGS = 1 << 62 | 1
TOKEN = "bostie-test-token-7c1f"
MISSING = re.compile(
    r"Object missing required field `(?P<key>[^`]+)`(?: - at `\$(?P<at>.*)`)?$"
)
NULL = re.compile(r"got `null`(?: - at `\$(?P<at>.*)`)?$")
SNOWFLAKE_KEY = re.compile(r"\.\d+(?=\.|\[|$)")
SHAPE_KEYS = {("GUILD_CREATE.unavailable", "$.unavailable")}
LAZY_KEYS = ("message", "triggering_interaction_metadata", "data")

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
        rest = (
            partial(decode, model) if isinstance(model, type) else decoder(model).decode
        )
        yield pytest.param(FIXTURES / "rest" / f"{name}.json", rest, id=name)
    for path in sorted(WEBHOOK_FIXTURES.glob("*.json")):
        if (event := WEBHOOK_EVENTS.get(path.stem)) is not None:
            yield pytest.param(
                path, partial(event.decode, 0), id=f"webhook_{path.stem}"
            )


ALL_FIXTURES = list(fixtures())


def load(path: Path) -> Any:
    document = msgspec.json.decode(path.read_bytes())
    webhook = path.parent == WEBHOOK_FIXTURES
    for key in ("event", "data") if webhook else PAYLOAD_AT.get(path.stem, ()):
        document = document[key]
    return document


def is_map(value: dict[str, Any], path: str) -> bool:
    keyed = path.endswith("_localizations") or all(key.isdigit() for key in value)
    return bool(value) and keyed


def objects(value: Any, path: str = "$") -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if not is_map(value, path):
            yield value
        for key, item in value.items():
            yield from objects(item, f"{path}.{key}")
    elif isinstance(value, list):
        for item in value:
            yield from objects(item, path)


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
            if not is_map(value, path):
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
            yield from walk(attribute(value, name))
    elif isinstance(value, tuple | list):
        for item in value:
            yield from walk(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk(item)


def attribute(struct: msgspec.Struct, name: str) -> object:
    public = name.lstrip("_")
    return getattr(struct, public if hasattr(type(struct), public) else name)


def entity(event: events.Event, document: dict[str, Any]) -> object:
    for field in msgspec.structs.fields(event):
        child = attribute(event, field.name)
        if isinstance(child, Model) and (
            field.encode_name not in document or child.raw_json is event.raw_json
        ):
            return child
    return None


def unmodelled(value: object, document: Any) -> Iterator[tuple[type, str]]:
    if isinstance(value, UnknownComponent):
        return
    if isinstance(value, msgspec.Struct) and isinstance(document, dict):
        names = {
            field.encode_name.lstrip("_"): field.name
            for field in msgspec.structs.fields(value)
        }
        for key, name in names.items():
            if key in document:
                yield from unmodelled(attribute(value, name), document[key])
        tag = value.__struct_config__.tag_field
        rest = {key: item for key, item in document.items() if key not in {*names, tag}}
        whole = entity(value, document) if isinstance(value, events.Event) else None
        if whole is not None:
            yield from unmodelled(whole, rest)
        else:
            yield from ((type(value), key) for key in rest)
    elif isinstance(value, dict) and isinstance(document, dict):
        for key, item in document.items():
            yield from unmodelled(value.get(int(key) if key.isdigit() else key), item)
    elif isinstance(value, tuple | list) and isinstance(document, list):
        for inner, item in zip(value, document, strict=False):
            yield from unmodelled(inner, item)


def tuple_of(value: object) -> tuple[object, ...]:
    return value if isinstance(value, tuple) else (value,)


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
            isinstance(original, dict) and is_map(original, where)
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
        return where.endswith(null["at"] or LAZY_KEYS)
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
def test_every_key_maps_to_a_field_of_its_model(path: Path, decoder: Decoder) -> None:
    document = load(path)

    decoded = decoder(msgspec.json.encode(document))

    unexpected = {
        f"{model.__name__}.{key}"
        for model, key in unmodelled(decoded, document)
        if not {f"{cls.__name__}.{key}" for cls in model.__mro__} & UNMODELLED_KEYS
    }
    assert unexpected == set()


@pytest.mark.parametrize(("path", "decoder"), ALL_FIXTURES)
def test_unknown_enum_values_and_flag_bits_are_kept(
    path: Path, decoder: Decoder
) -> None:
    document = load(path)
    for value in list(objects(document)):
        for key, item in value.items():
            if key in ENUM_KEYS and isinstance(item, int):
                value[key] = 9999
            elif key in ENUM_LIST_KEYS:
                value[key] = [9999]
            elif key in FLAG_KEYS and not isinstance(item, list):
                value[key] = (
                    str(UNKNOWN_FLAGS) if isinstance(item, str) else UNKNOWN_FLAGS
                )

    decoded = decode_all(decoder, document)

    enums = [
        value
        for struct in decoded
        if isinstance(struct, msgspec.Struct)
        for field in msgspec.structs.fields(struct)
        for value in tuple_of(getattr(struct, field.name))
        if isinstance(value, DiscordIntEnum) and value is not field.default
    ]
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
def test_no_repr_shows_a_token_raw_json_or_a_private_field(
    path: Path, decoder: Decoder
) -> None:
    document = load(path)
    for value in list(objects(document)):
        for key in SECRET_KEYS & value.keys():
            secret = value[key]
            value[key] = (
                dict.fromkeys(secret, TOKEN) if isinstance(secret, dict) else TOKEN
            )

    for decoded in decode_all(decoder, document):
        if isinstance(decoded, msgspec.Struct):
            for text in (repr(decoded), str(decoded), repr(decoded.__rich_repr__())):
                assert TOKEN not in text
            if not isinstance(decoded, UnknownComponent):
                assert "msgspec.Raw" not in repr(decoded)
                assert not [
                    name
                    for name, _ in decoded.__rich_repr__()
                    if name.startswith("_") or name == "raw_json"
                ]


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
    assert isinstance(created.guild, Guild)
    role_permissions = created.guild.roles[3].permissions
    assert role_permissions & bit
    assert Permissions.ADMINISTRATOR in role_permissions
    decoded_overwrite = created.channels[3].permission_overwrites[0]
    assert decoded_overwrite.allow == bit | Permissions.VIEW_CHANNEL
    assert decoded_overwrite.deny == bit
    assert received.app_permissions == bit
    resolved_channel = received.data.resolved.channels[int(channel_id)]
    assert resolved_channel.permissions == bit | Permissions.SEND_MESSAGES
