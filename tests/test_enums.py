import msgspec
import pytest

from lyrecord.enums import (
    ButtonStyle,
    Capabilities,
    ChannelFlags,
    ChannelType,
    CommandType,
    ComponentType,
    Intents,
    InteractionContextType,
    InteractionType,
    MessageFlags,
    MessageType,
    OptionType,
    Permissions,
)


class Channel(msgspec.Struct):
    type: ChannelType


class Role(msgspec.Struct):
    permissions: Permissions


def test_unknown_value_decodes_to_a_stable_pseudo_member() -> None:
    decoder = msgspec.json.Decoder(Channel, strict=False)

    first = decoder.decode(b'{"type": 9999}').type
    second = decoder.decode(b'{"type": 9999}').type

    assert first.name == "UNKNOWN_9999"
    assert first == 9999
    assert first is second is ChannelType(9999)
    assert ChannelType(9999) not in list(ChannelType)


def test_flags_keep_unknown_bits_from_a_string() -> None:
    bits = (1 << 60) | Permissions.BYPASS_SLOWMODE | Permissions.VIEW_CHANNEL
    decoder = msgspec.json.Decoder(Role, strict=False)

    permissions = decoder.decode(b'{"permissions": "%d"}' % bits).permissions

    assert permissions == bits
    assert Permissions.BYPASS_SLOWMODE in permissions


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
        (Capabilities.CHANNEL_OBFUSCATION, 1 << 15),
    ],
)
def test_values_match_the_docs(member: int, value: int) -> None:
    assert member == value
