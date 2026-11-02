from collections.abc import Callable

import msgspec
import pytest

from bostie import (
    Button,
    ButtonStyle,
    ChannelSelect,
    ChannelType,
    SelectDefaultValue,
    ui,
)
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
        "any", default_users=[1], default_roles=[2], placeholder="Anyone", max_values=2
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
        ui.string_select(custom_id, [ui.select_option("One", "1")])


def test_a_premium_button_sells_its_sku_and_builders_take_an_id() -> None:
    premium = ui.premium_button(1088510058284990888, id=7)

    assert premium == {
        "type": 2,
        "style": 6,
        "sku_id": 1088510058284990888,
        "disabled": False,
        "id": 7,
    }
    assert decode_components([msgspec.Raw(msgspec.json.encode(premium))]) == (
        Button(
            style=ButtonStyle.PREMIUM, sku_id=1088510058284990888, disabled=False, id=7
        ),
    )
    assert ui.text("hi", id=3) == {"type": 10, "content": "hi", "id": 3}


OPTION = ui.select_option("One", "1")
CHOICE = ui.group_option("One", "1")


@pytest.mark.parametrize(
    ("build", "problem"),
    [
        (lambda: ui.button("x" * 81, custom_id="b"), "label is 81 characters"),
        (lambda: ui.link_button("x", "h" * 513), "url is 513 characters"),
        (lambda: ui.row(*[ui.button("b", custom_id=str(n)) for n in range(6)]), "1-5"),
        (
            lambda: ui.row(ui.user_select("u"), ui.button("b", custom_id="b")),
            "one select",
        ),
        (lambda: ui.string_select("s", [OPTION] * 26), "takes 1-25 items"),
        (lambda: ui.string_select("s", [OPTION], placeholder="p" * 151), "placeholder"),
        (lambda: ui.string_select("s", [OPTION], min_values=0), "required=False"),
        (lambda: ui.user_select("u", min_values=3, max_values=2), "max_values"),
        (lambda: ui.user_select("u", defaults=[1, 2]), "2 defaults"),
        (lambda: ui.select_option("x" * 101, "1"), "label is 101"),
        (lambda: ui.section(*[ui.text("t")] * 4, accessory=ui.thumbnail("u")), "1-3"),
        (ui.media_gallery, "a media gallery takes 1-10"),
        (lambda: ui.gallery_item("u", description="d" * 1025), "description"),
        (lambda: ui.modal("m", "t" * 46, ui.text("t")), "title must be 1-45"),
        (lambda: ui.modal("m", "Title"), "takes 1-5 items"),
        (lambda: ui.label("l" * 46, ui.text_input("t")), "label is 46"),
        (lambda: ui.text_input("t", max_length=4001), "max_length must be 1-4000"),
        (lambda: ui.file_upload("f", max_values=11), "max_values must be 1-10"),
        (lambda: ui.radio_group("r", [CHOICE]), "takes 2-10 items"),
        (lambda: ui.checkbox_group("c", [CHOICE] * 11), "checkbox group takes 1-10"),
    ],
)
def test_builders_refuse_what_discord_would_reject(
    build: Callable[[], object], problem: str
) -> None:
    with pytest.raises(ValueError, match=problem):
        build()


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
        ui.raw({"type": 99, "future": True}),
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
            {"type": 99, "future": True},
        ],
        "accent_color": 0x5865F2,
        "spoiler": False,
    }


def test_ui_modal_builders_return_discord_modal_dicts() -> None:
    explanation = ui.text_input(
        "bug_explanation",
        style=ui.TextInputStyle.PARAGRAPH,
        min_length=1000,
        max_length=4000,
        placeholder="Write your explanation here...",
    )
    classes = [
        ui.group_option("Warrior", "warrior", description="Strong and brave"),
        ui.group_option("Rogue", "rogue", default=True),
    ]
    form = ui.modal(
        "bug_modal",
        "Bug Report",
        ui.text("Thanks for helping!"),
        ui.label(
            "Why is it your favorite?",
            explanation,
            description="Please provide as much detail as possible!",
        ),
        ui.label("Who saw it?", ui.user_select("seen_by", required=False)),
        ui.label("Screenshots", ui.file_upload("shots", max_values=10)),
        ui.label("Your class", ui.radio_group("class_radio", classes)),
    )

    assert form == {
        "custom_id": "bug_modal",
        "title": "Bug Report",
        "components": [
            {"type": 10, "content": "Thanks for helping!"},
            {
                "type": 18,
                "label": "Why is it your favorite?",
                "description": "Please provide as much detail as possible!",
                "component": {
                    "type": 4,
                    "custom_id": "bug_explanation",
                    "style": 2,
                    "min_length": 1000,
                    "max_length": 4000,
                    "placeholder": "Write your explanation here...",
                    "required": True,
                },
            },
            {
                "type": 18,
                "label": "Who saw it?",
                "component": {
                    "type": 5,
                    "custom_id": "seen_by",
                    "default_values": [],
                    "min_values": 1,
                    "max_values": 1,
                    "required": False,
                    "disabled": False,
                },
            },
            {
                "type": 18,
                "label": "Screenshots",
                "component": {
                    "type": 19,
                    "custom_id": "shots",
                    "min_values": 1,
                    "max_values": 10,
                    "required": True,
                },
            },
            {
                "type": 18,
                "label": "Your class",
                "component": {
                    "type": 21,
                    "custom_id": "class_radio",
                    "options": [
                        {
                            "value": "warrior",
                            "label": "Warrior",
                            "description": "Strong and brave",
                            "default": False,
                        },
                        {"value": "rogue", "label": "Rogue", "default": True},
                    ],
                    "required": True,
                },
            },
        ],
    }
    monday = ui.group_option("Monday", "mon")
    assert ui.checkbox_group("days", [monday], min_values=0, required=False) == {
        "type": 22,
        "custom_id": "days",
        "options": [{"value": "mon", "label": "Monday", "default": False}],
        "min_values": 0,
        "required": False,
    }
    assert ui.checkbox("agree", default=True) == {
        "type": 23,
        "custom_id": "agree",
        "default": True,
    }
    assert ui.file_upload("doc", file_types=[".pdf"])["file_types"] == [".pdf"]
    assert ui.text_input("name", value="Ann")["value"] == "Ann"
