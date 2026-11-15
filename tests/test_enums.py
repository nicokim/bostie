from enum import Enum, IntFlag

import msgspec
import pytest

from bostie.enums import (
    ApplicationFlags,
    ButtonStyle,
    ChannelFlags,
    ChannelType,
    CommandType,
    ComponentType,
    Intents,
    InteractionContextType,
    InteractionType,
    MessageActivityType,
    MessageFlags,
    MessageReferenceType,
    MessageType,
    OptionType,
    Permissions,
    Status,
)
from bostie.models import PartialApplication, PartialMember, PartialMessage, decode

MEMBER = b'{"roles": [], "joined_at": null, "flags": 0, "permissions": "%d"}'


class Channel(msgspec.Struct):
    type: ChannelType


class Presence(msgspec.Struct):
    type: Status


@pytest.mark.parametrize(
    ("struct", "enum", "value"),
    [(Channel, ChannelType, 9999), (Presence, Status, "streaming")],
)
def test_unknown_value_decodes_to_a_stable_pseudo_member(
    struct: type[Channel | Presence], enum: type[Enum], value: int | str
) -> None:
    decoder = msgspec.json.Decoder(struct, strict=False)
    payload = msgspec.json.encode({"type": value})

    first = decoder.decode(payload).type
    second = decoder.decode(payload).type

    assert first.name == f"UNKNOWN_{value}"
    assert first == value
    assert first is second is enum(value)
    assert enum(value) not in list(enum)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b'{"id": "1", "flags": 8388608}', ApplicationFlags.APPLICATION_COMMAND_BADGE),
        (
            b'{"id": "1", "flags_new": "%d"}'
            % ((1 << 70) | ApplicationFlags.APPLICATION_COMMAND_BADGE),
            (1 << 70) | ApplicationFlags.APPLICATION_COMMAND_BADGE,
        ),
    ],
)
def test_application_flags_read_flags_new_only_when_it_is_sent(
    payload: bytes, expected: int
) -> None:
    assert decode(PartialApplication, payload).flags == expected


@pytest.mark.parametrize(
    ("flags", "unknown"),
    [
        (Permissions, 1 << 70),
        (ApplicationFlags, 1 << 70),
        (MessageFlags, 1 << 63),
        (ChannelFlags, 1 << 63),
        (Intents, 1 << 40),
    ],
)
def test_every_operator_keeps_unknown_bits(flags: type[IntFlag], unknown: int) -> None:
    first, second = list(flags)[:2]
    value = flags(unknown | first | second)

    assert value & ~first == unknown | second
    assert value & ~flags(0) == value
    assert ~~value == value
    assert value | first == value
    assert value ^ first == unknown | second
    assert unknown & value == unknown
    assert unknown ^ value == first | second
    assert all(member in ~first for member in flags if member != first)
    assert msgspec.json.Decoder(flags).decode(b"%d" % first) == first


def test_clearing_a_message_flag_keeps_bits_discord_added_later() -> None:
    message = decode(
        PartialMessage,
        b'{"id": "1", "channel_id": "2", "flags": %d}' % (1 << 16 | 1 << 2),
    )

    assert message.flags & ~MessageFlags.SUPPRESS_EMBEDS == 1 << 16


def test_reading_permissions_caches_no_composite_values() -> None:
    cached = len(Permissions._value2member_map_)

    for value in range(1, 2000):
        member = decode(PartialMember, MEMBER % (value << 20))
        assert member.permissions == value << 20

    assert len(Permissions._value2member_map_) == cached


def test_default_intents_exclude_privileged_ones() -> None:
    privileged = (
        Intents.GUILD_MEMBERS | Intents.GUILD_PRESENCES | Intents.MESSAGE_CONTENT
    )

    assert Intents.DEFAULT.value == 53575421
    assert Intents.DEFAULT & privileged == 0
    assert Intents.DEFAULT | privileged == sum(Intents)


@pytest.mark.parametrize(
    ("member", "value"),
    [
        (ChannelType.GUILD_MEDIA, 16),
        (MessageType.POLL_RESULT, 46),
        (InteractionType.MODAL_SUBMIT, 5),
        (InteractionContextType.PRIVATE_CHANNEL, 2),
        (ComponentType.CONTAINER, 17),
        (ComponentType.CHECKBOX, 23),
        (ButtonStyle.PREMIUM, 6),
        (OptionType.ATTACHMENT, 11),
        (CommandType.PRIMARY_ENTRY_POINT, 4),
        (Permissions.SEND_VOICE_MESSAGES, 1 << 46),
        (Permissions.SET_VOICE_CHANNEL_STATUS, 1 << 48),
        (Permissions.BYPASS_SLOWMODE, 1 << 52),
        (Intents.DIRECT_MESSAGE_POLLS, 1 << 25),
        (MessageFlags.IS_COMPONENTS_V2, 1 << 15),
        (ChannelFlags.CHANNEL_OBFUSCATED, 1 << 17),
        (ApplicationFlags.APPLICATION_AUTO_MODERATION_RULE_CREATE_BADGE, 1 << 6),
        (ApplicationFlags.GATEWAY_MESSAGE_CONTENT_LIMITED, 1 << 19),
        (ApplicationFlags.APPLICATION_COMMAND_BADGE, 1 << 23),
        (MessageReferenceType.FORWARD, 1),
        (MessageActivityType.STREAM_REQUEST, 6),
    ],
    ids=lambda value: getattr(value, "name", str(value)),
)
def test_values_match_the_docs(member: int, value: int) -> None:
    assert member == value


def test_only_the_types_the_docs_mark_undeletable_are_undeletable() -> None:
    undeletable = {kind for kind in MessageType if not kind.deletable}

    assert undeletable == {
        MessageType.RECIPIENT_ADD,
        MessageType.RECIPIENT_REMOVE,
        MessageType.CALL,
        MessageType.CHANNEL_NAME_CHANGE,
        MessageType.CHANNEL_ICON_CHANGE,
        MessageType.THREAD_STARTER_MESSAGE,
    }
    assert MessageType(99).deletable
