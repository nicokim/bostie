import msgspec
import pytest

from bostie import ChannelSelect, ChannelType, SelectDefaultValue, ui
from bostie.models import decode_components


def test_ui_builders_return_discord_component_dicts() -> None:
    components = ui.row(
        ui.button("Helpful", custom_id="vote:rules:1", style=ui.ButtonStyle.SUCCESS),
        ui.button(None, custom_id="skip", emoji="\N{THUMBS UP SIGN}", disabled=True),
        ui.link_button("Docs", "https://example.com", emoji={"id": "1", "name": "d"}),
    )

    assert components == {
        "type": 1,
        "components": [
            {
                "type": 2,
                "style": 3,
                "label": "Helpful",
                "custom_id": "vote:rules:1",
                "disabled": False,
            },
            {
                "type": 2,
                "style": 2,
                "emoji": {"name": "\N{THUMBS UP SIGN}"},
                "custom_id": "skip",
                "disabled": True,
            },
            {
                "type": 2,
                "style": 5,
                "label": "Docs",
                "emoji": {"id": "1", "name": "d"},
                "url": "https://example.com",
                "disabled": False,
            },
        ],
    }


def test_ui_string_select_and_options() -> None:
    menu = ui.string_select(
        "pick",
        [
            ui.select_option("One", "1", default=True),
            ui.select_option("Two", "2", description="2nd"),
        ],
        placeholder="Choose",
        max_values=2,
    )

    assert menu == {
        "type": 3,
        "custom_id": "pick",
        "options": [
            {"label": "One", "value": "1", "default": True},
            {"label": "Two", "value": "2", "description": "2nd", "default": False},
        ],
        "placeholder": "Choose",
        "min_values": 1,
        "max_values": 2,
        "disabled": False,
    }


def test_ui_entity_selects_list_their_defaults_and_decode_back() -> None:
    who = ui.user_select("who", defaults=[1])
    mentionable = ui.mentionable_select(
        "any", default_users=[1], default_roles=[2], placeholder="Anyone"
    )
    voice = ui.channel_select(
        "where", channel_types=[ChannelType.GUILD_VOICE], defaults=[3]
    )

    assert who == {
        "type": 5,
        "custom_id": "who",
        "default_values": [{"id": 1, "type": "user"}],
        "min_values": 1,
        "max_values": 1,
        "disabled": False,
    }
    assert ui.role_select("roles", defaults=[2])["default_values"] == [
        {"id": 2, "type": "role"}
    ]
    assert mentionable["default_values"] == [
        {"id": 1, "type": "user"},
        {"id": 2, "type": "role"},
    ]
    assert mentionable["placeholder"] == "Anyone"
    decoded = decode_components([msgspec.Raw(msgspec.json.encode(voice))])
    assert decoded == (
        ChannelSelect(
            custom_id="where",
            channel_types=(ChannelType.GUILD_VOICE,),
            default_values=(SelectDefaultValue(id=3, type="channel"),),
            min_values=1,
            max_values=1,
            disabled=False,
        ),
    )


@pytest.mark.parametrize("custom_id", ["", "x" * 101])
def test_ui_checks_custom_id_length(custom_id: str) -> None:
    with pytest.raises(ValueError, match="custom_id must be 1-100 characters"):
        ui.button("x", custom_id=custom_id)
    with pytest.raises(ValueError, match="custom_id must be 1-100 characters"):
        ui.string_select(custom_id, [])


def test_ui_v2_builders_return_discord_component_dicts() -> None:
    card = ui.container(
        ui.section(
            ui.text("# Patch notes"),
            accessory=ui.thumbnail("https://example.com/logo.png", description="Logo"),
        ),
        ui.separator(spacing=2),
        ui.media_gallery(
            ui.gallery_item("attachment://shot.png", spoiler=True),
            ui.gallery_item("https://example.com/b.png", description="Before"),
        ),
        ui.file("notes.pdf"),
        ui.row(ui.link_button("Docs", "https://example.com")),
        accent_color=0x5865F2,
    )

    assert card == {
        "type": 17,
        "components": [
            {
                "type": 9,
                "components": [{"type": 10, "content": "# Patch notes"}],
                "accessory": {
                    "type": 11,
                    "media": {"url": "https://example.com/logo.png"},
                    "description": "Logo",
                    "spoiler": False,
                },
            },
            {"type": 14, "divider": True, "spacing": 2},
            {
                "type": 12,
                "items": [
                    {"media": {"url": "attachment://shot.png"}, "spoiler": True},
                    {
                        "media": {"url": "https://example.com/b.png"},
                        "description": "Before",
                        "spoiler": False,
                    },
                ],
            },
            {
                "type": 13,
                "file": {"url": "attachment://notes.pdf"},
                "spoiler": False,
            },
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Docs",
                        "url": "https://example.com",
                        "disabled": False,
                    }
                ],
            },
        ],
        "accent_color": 0x5865F2,
        "spoiler": False,
    }
