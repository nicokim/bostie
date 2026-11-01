import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
import pytest

from bostie import ui
from bostie.enums import (
    ButtonStyle,
    ChannelType,
    ComponentType,
    MessageFlags,
    MessageType,
    OptionType,
    Permissions,
)
from bostie.file import File
from bostie.models import (
    ActionRow,
    AllowedMentions,
    Application,
    Attachment,
    Button,
    Channel,
    Embed,
    EmbedField,
    Guild,
    Interaction,
    Member,
    Message,
    Model,
    Role,
    SelectMenu,
    UnknownComponent,
    User,
    build_message,
    decode,
    decode_components,
)

FIXTURES = Path(__file__).parent / "fixtures" / "models"


def payload(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("rest/user.json", User),
        ("rest/member.json", Member),
        ("rest/channel.json", Channel),
        ("rest/guild.json", Guild),
        ("rest/message.json", Message),
        ("rest/application_me.json", Application),
    ],
)
def test_rest_results_keep_their_payload(name: str, model: type[Model]) -> None:
    data = payload(name)

    value = decode(model, data)

    assert value.raw_json is data
    assert value.raw == json.loads(data)


def test_nested_objects_have_no_raw_payload() -> None:
    message = decode(Message, payload("rest/message.json"))

    assert message.author is not None
    assert message.author.raw is None
    assert message.referenced_message is not None
    assert message.referenced_message.raw is None


def test_snowflakes_permissions_and_timestamps_are_typed() -> None:
    member = decode(Member, payload("rest/member.json"))
    guild = decode(Guild, payload("events/GUILD_UPDATE.json"))
    interaction = decode(Interaction, payload("events/INTERACTION_CREATE.command.json"))

    assert member.user is not None
    assert member.user.id == 820774541721731073
    assert member.joined_at == datetime(2025, 4, 18, 19, 3, 11, 987000, tzinfo=UTC)
    assert guild.owner_id == 308882807193731073
    assert guild.features[0] == "ANIMATED_ICON"
    assert interaction.app_permissions == 4069281901182529
    assert interaction.app_permissions is not None
    assert Permissions.ATTACH_FILES in interaction.app_permissions


def test_rich_message() -> None:
    message = decode(Message, payload("events/MESSAGE_CREATE.rich.json"))

    assert message.type is MessageType.REPLY
    assert message.flags == MessageFlags.SUPPRESS_NOTIFICATIONS
    assert message.nonce == "1564609631957942275"
    assert [user.id for user in message.mentions] == [820774541721731073]
    assert message.mention_roles == (1335174302269571073,)
    assert [attachment.filename for attachment in message.attachments] == [
        "waveform.png",
        "SPOILER_notes.txt",
    ]
    assert message.attachments[0].width == 1280
    first, second = message.embeds
    assert first.timestamp == datetime(2026, 10, 27, 11, 59, tzinfo=UTC)
    assert first.fields[1] == EmbedField(
        name="Voice", value="DAVE (planned)", inline=True
    )
    assert second.video is not None
    assert second.video.url == "https://www.youtube.com/embed/q3Vx8RmT2kZ"
    assert [reaction.count for reaction in message.reactions] == [2, 1]
    assert message.reactions[1].emoji is not None
    assert message.reactions[1].emoji.id == 1345334752051331073
    assert message.sticker_items[0].name == "lyre wave"
    assert message.message_reference is not None
    assert message.referenced_message is not None
    assert message.message_reference.message_id == message.referenced_message.id
    assert message.jump_url == (
        "https://discord.com/channels/1335172792320131073/"
        "1335173295636611073/1564609632612253697"
    )


def test_components_decode_known_types_and_keep_unknown_ones() -> None:
    data = payload("events/MESSAGE_CREATE.rich.json")

    buttons, selects, hologram = decode(Message, data).components

    assert isinstance(buttons, ActionRow)
    close, docs = buttons.components
    assert isinstance(close, Button)
    assert close.style is ButtonStyle.DANGER
    assert close.custom_id == "ticket:close:42"
    assert isinstance(docs, Button)
    assert docs.url == "https://bostie.dev/docs"
    assert isinstance(selects, ActionRow)
    (select,) = selects.components
    assert isinstance(select, SelectMenu)
    assert select.type is ComponentType.STRING_SELECT
    assert [option.value for option in select.options] == [
        "ant",
        "butterfly",
        "lyrebird",
    ]
    assert isinstance(hologram, UnknownComponent)
    assert hologram.type == 99
    assert msgspec.json.decode(hologram.raw) == json.loads(data)["components"][2]


def test_a_component_with_a_changed_shape_does_not_hide_its_siblings() -> None:
    message = msgspec.json.decode(payload("events/MESSAGE_CREATE.rich.json"))
    message["components"][0]["components"][0]["style"] = "very red"

    row = decode(Message, msgspec.json.encode(message)).components[0]

    assert isinstance(row, ActionRow)
    changed, docs = row.components
    assert isinstance(changed, UnknownComponent)
    assert changed.type == ComponentType.BUTTON
    assert isinstance(docs, Button)


def test_reencoding_writes_components_back_unchanged() -> None:
    data = payload("events/MESSAGE_CREATE.rich.json")
    message = decode(Message, data)
    original = json.loads(data)["components"]

    body, _ = build_message(components=message.components)

    assert msgspec.json.decode(msgspec.json.encode(message))["components"] == original
    assert msgspec.json.decode(msgspec.json.encode(body))["components"] == original


def test_a_decoded_select_sent_back_keeps_its_default_values() -> None:
    default = {"id": "820774541721731073", "type": "user"}
    select = {"type": 5, "custom_id": "who", "default_values": [default]}
    row = {"type": 1, "components": [select]}
    message = decode(Message, msgspec.json.encode({"id": "1", "components": [row]}))
    decoded_row = message.components[0]
    assert isinstance(decoded_row, ActionRow)
    child = decoded_row.components[0]
    assert isinstance(child, SelectMenu)

    body, _ = build_message(components=[msgspec.structs.replace(child, disabled=True)])

    sent = msgspec.json.decode(msgspec.json.encode(body))["components"][0]
    assert sent["default_values"] == [{"id": 820774541721731073, "type": "user"}]
    assert sent["disabled"] is True


def test_a_component_interaction_links_its_message_to_the_guild() -> None:
    interaction = decode(Interaction, payload("events/INTERACTION_CREATE.button.json"))
    message = interaction.message
    assert message is not None

    assert message.jump_url == (
        f"https://discord.com/channels/{interaction.guild_id}/"
        f"{message.channel_id}/{message.id}"
    )
    assert message.referenced_message is not None
    assert message.referenced_message.guild_id == interaction.guild_id
    assert decode(Message, payload("rest/message.json")).jump_url.startswith(
        "https://discord.com/channels/@me/"
    )


def test_resolved_options_convert_to_their_models() -> None:
    interaction = decode(Interaction, payload("events/INTERACTION_CREATE.command.json"))
    assert interaction.data is not None
    resolved = interaction.data.resolved
    assert resolved is not None

    member = Member.from_resolved(resolved, 820774541721731073)
    user = User.from_resolved(resolved, 308882807193731073)
    role = Role.from_resolved(resolved, 1335174302269571073)
    channel = Channel.from_resolved(resolved, 1335173547294851073)
    attachment = Attachment.from_resolved(resolved, 1564609863064092673)

    assert member is not None
    assert member.user is not None
    assert member.user.username == "linus"
    assert member.permissions == 1817464907619905
    assert user is not None
    assert user.global_name == "Ada"
    assert role is not None
    assert role.name == "Moderator"
    assert channel is not None
    assert channel.type is ChannelType.GUILD_VOICE
    assert channel.app_permissions == 4069281901182529
    assert attachment is not None
    assert attachment.ephemeral is True
    assert Member.from_resolved(resolved, 1) is None


def test_option_values_keep_their_json_types() -> None:
    interaction = decode(Interaction, payload("events/INTERACTION_CREATE.command.json"))
    assert interaction.data is not None
    (group,) = interaction.data.options
    (subcommand,) = group.options

    values = {option.name: option.value for option in subcommand.options}

    assert group.type is OptionType.SUB_COMMAND_GROUP
    assert subcommand.type is OptionType.SUB_COMMAND
    assert values["mode"] == "thorough"
    assert values["count"] == 7
    assert values["ratio"] == 0.75
    assert values["loud"] is True
    assert values["target"] == "820774541721731073"


def test_author_comes_from_the_member_in_guilds_and_the_user_in_dms() -> None:
    guild = decode(Interaction, payload("events/INTERACTION_CREATE.command.json"))
    dm = decode(Interaction, payload("events/INTERACTION_CREATE.command_dm.json"))
    assert dm.data is not None
    assert dm.data.resolved is not None

    assert guild.author is not None
    assert guild.author.username == "grace"
    assert dm.author is not None
    assert dm.author.username == "grace"
    assert dm.member is None
    assert Member.from_resolved(dm.data.resolved, 820774541721731073) is None
    assert User.from_resolved(dm.data.resolved, 820774541721731073) is not None


def test_component_and_autocomplete_data() -> None:
    button = decode(Interaction, payload("events/INTERACTION_CREATE.button.json"))
    select = decode(Interaction, payload("events/INTERACTION_CREATE.select.json"))
    autocomplete = decode(
        Interaction, payload("events/INTERACTION_CREATE.autocomplete.json")
    )
    assert button.data is not None
    assert select.data is not None
    assert autocomplete.data is not None

    assert button.data.custom_id == "ticket:close:42"
    assert button.data.component_type is ComponentType.BUTTON
    assert button.message is not None
    assert len(button.message.components) == 3
    assert select.data.values == ("ant", "lyrebird")
    assert [option.focused for option in autocomplete.data.options] == [None, True]


def test_obfuscation_is_read_from_the_flags_not_the_name() -> None:
    obfuscated = decode(Channel, payload("events/CHANNEL_UPDATE.obfuscated.json"))
    named_like_it = msgspec.structs.replace(obfuscated, flags=None)

    assert obfuscated.obfuscated
    assert not named_like_it.obfuscated


def test_mentions_and_display_names() -> None:
    member = decode(Member, payload("rest/member.json"))
    user = decode(User, payload("rest/user.json"))
    role = decode(Role, b'{"id": "42"}')
    channel = decode(Channel, b'{"id": "7"}')

    assert user.mention == "<@308882807193731073>"
    assert member.mention == "<@820774541721731073>"
    assert role.mention == "<@&42>"
    assert channel.mention == "<#7>"
    assert user.display_name == "Ada"
    assert decode(User, b'{"id": "1"}').display_name == ""
    assert member.display_name == "linus"
    assert msgspec.structs.replace(member, nick="Lin").display_name == "Lin"
    assert decode(Member, b"{}").display_name == ""


def test_build_message_sets_only_the_given_parts() -> None:
    assert build_message("hello") == ({"content": "hello"}, ())
    assert build_message(components=[], embeds=[]) == (
        {"components": [], "embeds": []},
        (),
    )
    assert build_message(tts=False, flags=MessageFlags.SUPPRESS_EMBEDS) == (
        {"tts": False, "flags": MessageFlags.SUPPRESS_EMBEDS},
        (),
    )
    misspelled: dict[str, Any] = {"embed": Embed(title="x")}
    with pytest.raises(TypeError, match="unexpected message parts: embed"):
        build_message(**misspelled)


def test_build_message_lists_files_as_attachments() -> None:
    files = [File("log.txt", b"ok"), File("chart.png", b"\x89PNG", "Weekly chart")]

    body, sent = build_message("report", files=files, reply_to=1564609610244030465)

    assert sent is files
    assert msgspec.json.decode(msgspec.json.encode(body)) == {
        "content": "report",
        "message_reference": {"type": 0, "message_id": 1564609610244030465},
        "attachments": [
            {"id": 0, "filename": "log.txt"},
            {"id": 1, "filename": "chart.png", "description": "Weekly chart"},
        ],
    }


def test_empty_allowed_mentions_silence_every_mention() -> None:
    body, _ = build_message("@everyone", allowed_mentions=AllowedMentions(parse=()))

    assert msgspec.json.encode(body) == (
        b'{"content":"@everyone","allowed_mentions":{"parse":[]}}'
    )


def test_build_message_never_writes_raw_payloads() -> None:
    message = decode(Message, payload("events/MESSAGE_CREATE.rich.json"))

    body, _ = build_message(
        "copy",
        embeds=[*message.embeds, Embed(title="new"), {"title": "dict"}],
        components=message.components,
        allowed_mentions=AllowedMentions(users=(820774541721731073,)),
    )

    assert b"__bostie_raw__" not in msgspec.json.encode(body)


def test_build_message_keeps_listed_attachments_before_new_files() -> None:
    kept = decode(Attachment, b'{"id": "1564609863064092673", "filename": "a.png"}')

    body, _ = build_message(
        files=[File("new.png", b"", spoiler=True)], attachments=[kept, 42]
    )

    assert body == {
        "attachments": [
            {"id": 1564609863064092673},
            {"id": 42},
            {"id": 0, "filename": "new.png", "is_spoiler": True},
        ]
    }
    assert build_message(attachments=[42])[0] == {"attachments": [{"id": 42}]}
    assert "attachments" not in build_message("text only")[0]


def test_v2_components_set_their_flag_clear_old_content_and_refuse_new_content() -> (
    None
):
    received = UnknownComponent(type=17, raw=msgspec.Raw(b'{"type":17}'))
    legacy_row = ui.row(ui.button("Go", custom_id="go"))

    with_text, _ = build_message(
        components=[ui.text("hi")], flags=MessageFlags.SUPPRESS_NOTIFICATIONS
    )
    resent, _ = build_message(components=[received])
    legacy, _ = build_message("hi", components=[legacy_row])

    v2 = MessageFlags.IS_COMPONENTS_V2
    assert with_text == {
        "components": [ui.text("hi")],
        "flags": v2 | MessageFlags.SUPPRESS_NOTIFICATIONS,
        "content": None,
        "embeds": [],
    }
    assert resent["flags"] == v2
    assert build_message(flags=v2)[0] == {"flags": v2, "content": None, "embeds": []}
    assert legacy.keys() == {"content", "components"}
    with pytest.raises(ValueError, match="Components V2"):
        build_message("hi", components=[ui.text("hi")])
    with pytest.raises(ValueError, match="Components V2"):
        build_message(embeds=[Embed(title="x")], flags=v2)


def test_text_inputs_and_deprecated_role_colors_are_not_modelled() -> None:
    text_input = msgspec.Raw(b'{"type": 4, "custom_id": "note", "label": "Note"}')
    role = decode(Role, b'{"id": "42", "color": 3447003}')

    (component,) = decode_components([text_input])

    assert isinstance(component, UnknownComponent)
    assert component.type == ComponentType.TEXT_INPUT
    assert "color" not in Role.__struct_fields__
    assert role.raw == {"id": "42", "color": 3447003}
