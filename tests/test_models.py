import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
import pytest

from bostie import ui
from bostie.enums import (
    ApplicationIntegrationType,
    AttachmentFlags,
    ButtonStyle,
    ChannelFlags,
    ChannelType,
    ComponentType,
    ContentScanMetadataFlags,
    EmbedFlags,
    EmbedMediaFlags,
    EmbedType,
    ExplicitContentFilterLevel,
    GuildFeature,
    IntegrationType,
    MessageActivityType,
    MessageFlags,
    MessageReferenceType,
    MessageType,
    MFALevel,
    OptionType,
    Permissions,
    PremiumType,
    Status,
    SystemChannelFlags,
    TeamMemberRole,
    UnfurledMediaItemFlags,
    UserFlags,
    WebhookEventType,
)
from bostie.file import File
from bostie.models import (
    ActionRow,
    AllowedMentions,
    Application,
    Attachment,
    AutocompleteInteraction,
    Button,
    Channel,
    ChannelSelect,
    CommandInteraction,
    CommandInteractionMetadata,
    ComponentInteraction,
    ComponentInteractionMetadata,
    Container,
    ContentScanMetadata,
    CurrentUser,
    Embed,
    EmbedField,
    Entitlement,
    FileComponent,
    Guild,
    GuildTemplate,
    GuildWidget,
    Integration,
    InteractionMetadata,
    MediaGallery,
    Member,
    Message,
    ModalInteractionMetadata,
    ModalSubmitInteraction,
    Model,
    PartialChannel,
    PartialEntitlement,
    ReactionCountDetails,
    Role,
    Section,
    Separator,
    StringSelect,
    SubmittedCheckbox,
    SubmittedFileUpload,
    SubmittedLabel,
    SubmittedRadioGroup,
    SubmittedStringSelect,
    SubmittedTextDisplay,
    SubmittedTextInput,
    TeamMember,
    TextDisplay,
    Thumbnail,
    UnknownComponent,
    UnknownInteraction,
    User,
    UserSelect,
    build_message,
    decode,
    decode_interaction,
)
from bostie.models.base import decoder
from bostie.models.message import decode_interaction_metadata, message_body

FIXTURES = Path(__file__).parent / "fixtures" / "models"
MESSAGE = json.loads((FIXTURES / "events" / "MESSAGE_CREATE.json").read_bytes())
ROLE = json.loads((FIXTURES / "events" / "GUILD_ROLE_CREATE.json").read_bytes())["role"]


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
    ],
)
def test_rest_results_keep_their_payload(name: str, model: type[Model]) -> None:
    data = payload(name)

    value = decode(model, data)

    assert value.raw_json is data
    assert value.raw == json.loads(data)


def test_nested_objects_have_no_raw_payload() -> None:
    message = decode(Message, payload("rest/message.json"))

    assert message.author.raw is None
    assert message.referenced_message is not None
    assert message.referenced_message.raw is None


def test_snowflakes_permissions_and_timestamps_are_typed() -> None:
    member = decode(Member, payload("rest/member.json"))
    guild = decode(Guild, payload("events/GUILD_UPDATE.json"))
    interaction = decode(
        CommandInteraction, payload("events/INTERACTION_CREATE.command.json")
    )

    assert member.user.id == 820774541721731073
    assert member.joined_at == datetime(2025, 4, 18, 19, 3, 11, 987000, tzinfo=UTC)
    assert guild.owner_id == 308882807193731073
    assert interaction.app_permissions == 4069281901182529
    assert Permissions.ATTACH_FILES in interaction.app_permissions


def test_a_guild_carries_its_settings_roles_and_expressions() -> None:
    guild = decode(Guild, payload("rest/guild.json"))

    assert guild.features[0] is GuildFeature.ANIMATED_ICON
    assert guild.mfa_level is MFALevel.NONE
    assert guild.explicit_content_filter is ExplicitContentFilterLevel.ALL_MEMBERS
    assert guild.system_channel_flags == (
        SystemChannelFlags.SUPPRESS_GUILD_REMINDER_NOTIFICATIONS
    )
    assert (guild.afk_timeout, guild.premium_subscription_count) == (300, 7)
    assert guild.roles[0].name == "@everyone"
    assert guild.emojis[0].name == "lyre"
    assert guild.stickers[0].name == "lyre wave"


def test_a_role_tag_sent_as_null_is_true_and_a_missing_one_false() -> None:
    tags = {"integration_id": "5", "premium_subscriber": None}

    booster = decode(Role, msgspec.json.encode(ROLE | {"tags": tags})).tags

    assert booster is not None
    assert (booster.integration_id, booster.bot_id) == (5, None)
    assert booster.premium_subscriber
    assert not booster.available_for_purchase
    assert not booster.guild_connections
    assert decode(Role, msgspec.json.encode(ROLE)).tags is None


def test_a_template_types_the_guild_it_creates() -> None:
    template = decode(GuildTemplate, payload("rest/guild_template.json"))

    guild = template.serialized_source_guild
    (everyone,) = guild.roles
    category, general = guild.channels

    assert (everyone.id, everyone.name) == (0, "@everyone")
    assert Permissions.SEND_MESSAGES in everyone.permissions
    assert category.type is ChannelType.GUILD_CATEGORY
    assert (general.id, general.parent_id) == (2, category.id)
    assert guild.system_channel_id == general.id


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
    super_reaction = message.reactions[1]
    assert super_reaction.emoji.id == 1345334752051331073
    assert super_reaction.count_details == ReactionCountDetails(burst=1, normal=0)
    assert super_reaction.burst_colors == ("#5865f2", "#eb459e")
    assert message.sticker_items[0].name == "lyre wave"
    assert message.message_reference is not None
    assert message.referenced_message is not None
    assert message.message_reference.message_id == message.referenced_message.id
    assert message.jump_url == (
        "https://discord.com/channels/1335172792320131073/"
        "1335173295636611073/1564609632612253697"
    )


def test_a_forward_carries_the_forwarded_message_as_a_snapshot() -> None:
    message = decode(Message, payload("events/MESSAGE_CREATE.forward.json"))

    (snapshot,) = message.message_snapshots
    forwarded = snapshot.message

    assert message.message_reference is not None
    assert message.message_reference.type is MessageReferenceType.FORWARD
    assert forwarded.content.startswith("Release notes are out")
    assert forwarded.attachments[0].filename == "SPOILER_notes.txt"
    assert forwarded.mention_roles == (1335174302269571073,)
    assert forwarded.sticker_items[0].name == "lyre wave"
    assert forwarded.components == ()


def test_interaction_metadata_decodes_by_type_and_keeps_unknown_types() -> None:
    document = json.loads(payload("events/MESSAGE_CREATE.interaction.json"))
    shared = document["interaction_metadata"]["triggering_interaction_metadata"]
    run = {**shared, "type": 2, "target_message_id": "42"}

    modal = decode(Message, msgspec.json.encode(document)).interaction_metadata
    command = decode_interaction_metadata(msgspec.json.encode(run))
    future = decode_interaction_metadata(msgspec.json.encode(run | {"type": 9}))

    assert isinstance(modal, ModalInteractionMetadata)
    assert modal.user.id == 820774541721731073
    assert modal.authorizing_integration_owners == {
        ApplicationIntegrationType.GUILD_INSTALL: 1335172792320131073
    }
    component = modal.triggering_interaction_metadata
    assert isinstance(component, ComponentInteractionMetadata)
    assert component.interacted_message_id == 1564615190094217217
    assert isinstance(command, CommandInteractionMetadata)
    assert command.target_message_id == 42
    assert type(future) is InteractionMetadata
    assert decode(Message, payload("rest/message.json")).interaction_metadata is None


def test_the_rarer_message_parts_decode_into_their_models() -> None:
    parts = {
        "application_id": "1329027298099331073",
        "activity": {"type": 1, "party_id": "lobby:7"},
        "application": {"id": "1329027298099331073", "name": "Lyre Hero"},
        "mention_channels": [
            {"id": "7", "guild_id": "8", "type": 0, "name": "news"},
        ],
        "role_subscription_data": {
            "role_subscription_listing_id": "9",
            "tier_name": "Gold",
            "total_months_subscribed": 3,
            "is_renewal": True,
        },
        "resolved": {"roles": {"1335174302269571073": ROLE}},
        "call": {"participants": ["820774541721731073"], "ended_timestamp": None},
    }

    message = decode(Message, msgspec.json.encode(MESSAGE | parts))

    assert message.application_id == 1329027298099331073
    assert message.activity is not None
    assert message.activity.type is MessageActivityType.JOIN
    assert message.application is not None
    assert message.application.id == 1329027298099331073
    assert message.mention_channels[0].type is ChannelType.GUILD_TEXT
    assert message.role_subscription_data is not None
    assert message.role_subscription_data.is_renewal
    assert message.resolved is not None
    assert message.resolved.roles[1335174302269571073].name == ROLE["name"]
    assert message.call is not None
    assert message.call.participants == (820774541721731073,)


def test_embeds_and_media_items_carry_their_content_scan_flags() -> None:
    first, video = decode(Message, payload("events/MESSAGE_CREATE.rich.json")).embeds
    container = decode(
        Message, payload("events/MESSAGE_CREATE.components_v2.json")
    ).components[0]
    text = {"type": 10, "content": "A preview"}
    preview = {"type": "components", "flags": 64, "components": [text]}

    assert (first.type, video.type) == (EmbedType.RICH, EmbedType.VIDEO)
    assert first.thumbnail is not None
    assert first.thumbnail.flags == EmbedMediaFlags.IS_ANIMATED
    assert first.thumbnail.content_type == "image/gif"
    assert isinstance(container, Container)
    gallery = container.components[1]
    assert isinstance(gallery, MediaGallery)
    media = gallery.items[0].media
    assert media.flags == UnfurledMediaItemFlags(0)
    assert media.content_scan_metadata == ContentScanMetadata(
        flags=ContentScanMetadataFlags(0)
    )
    link = decoder(Embed).decode(msgspec.json.encode(preview))
    assert link.flags == EmbedFlags.CONTAINS_GORE_CONTENT
    assert link.components == (TextDisplay(content="A preview"),)


def test_attachments_carry_voice_message_and_clip_details() -> None:
    message = decode(Message, payload("events/MESSAGE_CREATE.voice.json"))
    clip = json.loads(payload("events/MESSAGE_CREATE.rich.json"))["attachments"][0]
    clip |= {
        "flags": AttachmentFlags.IS_CLIP,
        "clip_participants": [MESSAGE["author"]],
        "clip_created_at": "2026-10-27T11:58:00+00:00",
        "application": {"id": "1329027298099331073"},
    }

    (voice,) = message.attachments
    decoded_clip = decode(Attachment, msgspec.json.encode(clip))

    assert MessageFlags.IS_VOICE_MESSAGE in message.flags
    assert voice.duration_secs == 2.2
    assert voice.waveform is not None
    assert (len(voice.waveform), max(voice.waveform)) == (22, 255)
    assert decoded_clip.placeholder_version == 1
    assert decoded_clip.clip_participants[0].username == "linus"
    assert decoded_clip.clip_created_at == datetime(2026, 10, 27, 11, 58, tzinfo=UTC)
    assert decoded_clip.application is not None


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
    assert isinstance(select, StringSelect)
    assert select.type is ComponentType.STRING_SELECT
    assert [option.value for option in select.options] == [
        "ant",
        "butterfly",
        "lyrebird",
    ]
    assert isinstance(hologram, UnknownComponent)
    assert hologram.type == 99
    assert msgspec.json.decode(hologram.raw) == json.loads(data)["components"][2]


def test_components_v2_decode_into_their_models() -> None:
    message = decode(Message, payload("events/MESSAGE_CREATE.components_v2.json"))

    container, file = message.components

    assert isinstance(container, Container)
    assert (container.type, container.accent_color) == (ComponentType.CONTAINER, 703487)
    heading, gallery, separator, section, buttons, picker = container.components
    assert isinstance(heading, TextDisplay)
    assert heading.content == "# You have encountered a wild coyote!"
    assert isinstance(gallery, MediaGallery)
    (item,) = gallery.items
    assert (item.media.width, item.media.content_type) == (1600, "image/webp")
    assert isinstance(separator, Separator)
    assert (separator.divider, separator.spacing) == (True, 2)
    assert isinstance(section, Section)
    assert [type(child) for child in section.components] == [TextDisplay]
    assert isinstance(section.accessory, Thumbnail)
    assert section.accessory.description == "A wild coyote"
    assert isinstance(buttons, ActionRow)
    assert [type(child) for child in buttons.components] == [Button, Button]
    assert isinstance(picker, ActionRow)
    (where,) = picker.components
    assert isinstance(where, ChannelSelect)
    assert where.channel_types == (
        ChannelType.GUILD_VOICE,
        ChannelType.GUILD_STAGE_VOICE,
    )
    assert where.default_values[0].id == 1335173547294851073
    assert isinstance(file, FileComponent)
    assert (file.name, file.size, file.file.attachment_id) == (
        "manual.pdf",
        482113,
        message.attachments[0].id,
    )


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
    message = decode(Message, msgspec.json.encode(MESSAGE | {"components": [row]}))
    decoded_row = message.components[0]
    assert isinstance(decoded_row, ActionRow)
    child = decoded_row.components[0]
    assert isinstance(child, UserSelect)

    body, _ = build_message(components=[msgspec.structs.replace(child, disabled=True)])

    sent = msgspec.json.decode(msgspec.json.encode(body))["components"][0]
    assert sent["default_values"] == [{"id": 820774541721731073, "type": "user"}]
    assert sent["disabled"] is True


def test_a_component_interaction_links_its_message_to_the_guild() -> None:
    interaction = decode(
        ComponentInteraction, payload("events/INTERACTION_CREATE.button.json")
    )
    message = interaction.message

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
    resolved = decode(
        CommandInteraction, payload("events/INTERACTION_CREATE.command.json")
    ).data.resolved

    member = Member.from_resolved(resolved, 820774541721731073)
    user = User.from_resolved(resolved, 308882807193731073)
    role = Role.from_resolved(resolved, 1335174302269571073)
    channel = PartialChannel.from_resolved(resolved, 1335173547294851073)
    attachment = Attachment.from_resolved(resolved, 1564609863064092673)

    assert member is not None
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
    data = decode(
        CommandInteraction, payload("events/INTERACTION_CREATE.command.json")
    ).data
    (group,) = data.options
    (subcommand,) = group.options

    values = {option.name: option.value for option in subcommand.options}

    assert group.type is OptionType.SUB_COMMAND_GROUP
    assert subcommand.type is OptionType.SUB_COMMAND
    assert values["mode"] == "thorough"
    assert values["count"] == 7
    assert values["ratio"] == 0.75
    assert values["loud"] is True
    assert values["target"] == "820774541721731073"


def test_the_user_comes_from_the_member_in_guilds_and_the_user_in_dms() -> None:
    guild = decode(
        CommandInteraction, payload("events/INTERACTION_CREATE.command.json")
    )
    dm = decode(
        CommandInteraction, payload("events/INTERACTION_CREATE.command_dm.json")
    )

    assert guild.member is not None
    assert guild.user is guild.member.user
    assert guild.user.username == "grace"
    assert dm.user.username == "grace"
    assert dm.member is None
    assert Member.from_resolved(dm.data.resolved, 820774541721731073) is None
    assert User.from_resolved(dm.data.resolved, 820774541721731073) is not None


def test_interactions_decode_by_type_and_unknown_types_keep_their_payload() -> None:
    names = ("command", "button", "select", "autocomplete", "modal")
    future = json.loads(payload("events/INTERACTION_CREATE.command.json")) | {
        "type": 99
    }

    command, button, select, autocomplete, modal = (
        decode_interaction(payload(f"events/INTERACTION_CREATE.{name}.json"))
        for name in names
    )
    unknown = decode_interaction(msgspec.json.encode(future))

    assert isinstance(command, CommandInteraction)
    assert isinstance(button, ComponentInteraction)
    assert button.data.custom_id == "ticket:close:42"
    assert button.data.component_type is ComponentType.BUTTON
    assert len(button.message.components) == 3
    assert isinstance(select, ComponentInteraction)
    assert select.data.values == ("ant", "lyrebird")
    assert isinstance(autocomplete, AutocompleteInteraction)
    assert [option.focused for option in autocomplete.data.options] == [False, True]
    assert isinstance(modal, ModalSubmitInteraction)
    assert (modal.data.custom_id, modal.user.id) == ("feedback:42", 820774541721731073)
    assert modal.token
    assert isinstance(unknown, UnknownInteraction)
    assert unknown.type == 99
    assert unknown.raw == future


def test_an_interaction_carries_its_guild_owners_and_entitlements() -> None:
    document = json.loads(payload("events/INTERACTION_CREATE.command.json"))
    (paid,) = document["entitlements"]
    test = {key: value for key, value in paid.items() if not key.endswith("_at")}
    document["entitlements"].append(test)

    interaction = decode(CommandInteraction, msgspec.json.encode(document))

    assert interaction.guild is not None
    assert interaction.guild.id == interaction.guild_id
    assert GuildFeature.ROLE_ICONS in interaction.guild.features
    assert interaction.authorizing_integration_owners == {
        ApplicationIntegrationType.GUILD_INSTALL: interaction.guild_id
    }
    subscription, test_entitlement = interaction.entitlements
    assert isinstance(subscription, Entitlement)
    assert subscription.ends_at == datetime(2026, 11, 1, 9, tzinfo=UTC)
    assert type(test_entitlement) is PartialEntitlement


def test_a_modal_submit_decodes_each_answer_inside_its_label() -> None:
    document = json.loads(payload("events/INTERACTION_CREATE.modal.json"))
    document["data"]["components"].append({"type": 99, "id": 10})

    modal = decode(ModalSubmitInteraction, msgspec.json.encode(document))

    display, *labels, future = modal.data.components
    answers = [label.component for label in labels if isinstance(label, SubmittedLabel)]
    assert display == SubmittedTextDisplay(id=1)
    assert answers == [
        SubmittedTextInput(
            id=3, custom_id="feedback", value="The new queue command is great"
        ),
        SubmittedRadioGroup(id=5, custom_id="rating", value="great"),
        SubmittedFileUpload(
            id=7, custom_id="screenshots", values=(1564615271093121025,)
        ),
        SubmittedCheckbox(id=9, custom_id="subscribe", value=True),
        SubmittedStringSelect(id=11, custom_id="topics", values=("queue", "voice")),
    ]
    assert modal.data.resolved.attachments[1564615271093121025].filename == (
        "queue.png"
    )
    assert isinstance(future, UnknownComponent)
    assert future.type == 99
    assert modal.message is None


def test_obfuscation_is_read_from_the_flags_not_the_name() -> None:
    obfuscated = decode(Channel, payload("events/CHANNEL_UPDATE.obfuscated.json"))
    named_like_it = msgspec.structs.replace(obfuscated, flags=ChannelFlags(0))

    assert obfuscated.obfuscated
    assert not named_like_it.obfuscated


def test_the_current_user_carries_its_account_settings() -> None:
    user = decode(CurrentUser, payload("events/USER_UPDATE.json"))

    assert (user.mfa_enabled, user.verified, user.email) == (True, True, None)
    assert (user.locale, user.premium_type) == ("en-US", PremiumType.NONE)
    assert user.flags == UserFlags(0)


def test_documented_string_values_decode_into_their_enums() -> None:
    app = json.loads(payload("rest/application_me.json"))
    app["event_webhooks_types"] = ["APPLICATION_AUTHORIZED", "SOMETHING_NEW"]
    member = {"membership_state": 2, "team_id": "1", "user": MESSAGE["author"]}

    integration = decode(Integration, payload("events/INTEGRATION_UPDATE.json"))
    widget = decode(GuildWidget, payload("rest/guild_widget.json"))
    developer = decode(TeamMember, msgspec.json.encode(member | {"role": "developer"}))
    webhooks = decode(Application, msgspec.json.encode(app)).event_webhooks_types

    assert integration.type is IntegrationType.TWITCH
    assert widget.members[0].status is Status.ONLINE
    assert developer.role is TeamMemberRole.DEVELOPER
    assert webhooks[0] is WebhookEventType.APPLICATION_AUTHORIZED
    assert webhooks[1].name == "UNKNOWN_SOMETHING_NEW"


def test_mentions_and_display_names() -> None:
    member = decode(Member, payload("rest/member.json"))
    user = decode(User, payload("rest/user.json"))
    role = decode(Role, msgspec.json.encode(ROLE | {"id": "42"}))
    channel = decode(Channel, b'{"id": "7", "type": 0}')

    assert user.mention == "<@308882807193731073>"
    assert member.mention == "<@820774541721731073>"
    assert role.mention == "<@&42>"
    assert channel.mention == "<#7>"
    assert user.display_name == "Ada"
    assert msgspec.structs.replace(user, global_name=None).display_name == user.username
    assert member.display_name == "linus"
    assert msgspec.structs.replace(member, nick="Lin").display_name == "Lin"


def test_a_messages_author_member_joins_its_member_and_author() -> None:
    message = decode(Message, payload("events/MESSAGE_CREATE.json"))
    member = message.author_member

    assert member is not None
    assert message.member is not None
    assert (member.user, member.flags) == (message.author, message.member.flags)
    assert msgspec.structs.replace(message, member=None).author_member is None


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


def test_an_edit_body_keeps_listed_attachments_and_sends_none_as_null() -> None:
    kept = decode(Message, payload("events/MESSAGE_CREATE.rich.json")).attachments[0]
    hidden = msgspec.structs.replace(
        kept, description="Alt text", flags=AttachmentFlags.IS_SPOILER
    )
    new = File("new.png", b"", spoiler=True, title="New chart")

    body, _ = message_body(None, {"files": [new], "attachments": [hidden, 42]})

    assert body == {
        "attachments": [
            {"id": kept.id, "description": "Alt text", "is_spoiler": True},
            {"id": 42},
            {"id": 0, "filename": "new.png", "title": "New chart", "is_spoiler": True},
        ]
    }
    cleared = {"content": None, "embeds": None, "attachments": None}
    assert message_body(None, cleared)[0] == cleared
    assert message_body(None, {"attachments": []})[0] == {"attachments": []}
    assert "attachments" not in message_body("text only", {})[0]


def test_v2_components_set_their_flag_and_clear_or_refuse_content() -> None:
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
    with pytest.raises(ValueError, match="Components V2"):
        message_body(None, {"components": [ui.text("hi")], "sticker_ids": [1]})
