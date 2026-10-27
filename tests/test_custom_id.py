import asyncio
import inspect
import re
from typing import get_args

import pytest

from lyrecord import ui
from lyrecord.custom_id import ComponentRoute
from lyrecord.router import Router


async def handler(ctx: object, **params: object) -> None:
    pass


def route(template: str) -> ComponentRoute[...]:
    return ComponentRoute(template, handler, router=Router())


def test_placeholders_parse_with_backtracking_and_int_conversion() -> None:
    vote = route("vote:{tag}:{up:int}")

    assert vote.names == ("tag", "up")
    assert vote.match("vote:a:b:1") == {"tag": "a:b", "up": 1}
    assert vote.match("vote:x:-5") == {"tag": "x", "up": -5}
    assert vote.match("vote:x:y") is None
    assert vote.match("vote::1") is None
    assert vote.match("other:x:1") is None


def test_literal_text_is_matched_exactly() -> None:
    assert route("a.b+c").match("a.b+c") == {}
    assert route("a.b+c").match("axb+c") is None


@pytest.mark.parametrize(
    "template",
    ["{a}{b}", "x:{a b}", "x:{a:float}", "x:{1a}", "x{", "x}", "{{a}}", "{a}:{a}"],
)
def test_invalid_templates(template: str) -> None:
    with pytest.raises(ValueError, match=re.escape(repr(template))):
        route(template)


def test_id_round_trips() -> None:
    vote = route("vote:{tag}:{up:int}")

    custom_id = vote.id(tag="rules", up=1)

    assert custom_id == "vote:rules:1"
    assert vote.match(custom_id) == {"tag": "rules", "up": 1}


@pytest.mark.parametrize(
    ("params", "error", "problem"),
    [
        ({"tag": "x"}, TypeError, "takes \\['tag', 'up'\\]"),
        ({"tag": "x", "up": 1, "extra": 2}, TypeError, "takes"),
        ({"tag": "x" * 100, "up": 1}, ValueError, "107 characters; Discord allows 100"),
        ({"tag": "", "up": 1}, ValueError, "does not parse back"),
        ({"tag": "x", "up": "1"}, ValueError, "does not parse back"),
    ],
)
def test_id_rejects_params_that_cannot_round_trip(
    params: dict[str, str | int], error: type[Exception], problem: str
) -> None:
    with pytest.raises(error, match=problem):
        route("vote:{tag}:{up:int}").id(**params)


def test_id_rejects_an_ambiguous_split() -> None:
    with pytest.raises(ValueError, match="does not parse back"):
        route("{a}:{b}").id(a="x:y", b="z")


def test_a_route_still_calls_its_handler() -> None:
    seen: list[tuple[object, str, int]] = []

    async def vote(ctx: object, tag: str, up: int) -> None:
        seen.append((ctx, tag, up))

    vote_route = ComponentRoute("vote:{tag}:{up:int}", vote, router=Router())
    asyncio.run(vote_route("ctx", "rules", up=1))

    assert seen == [("ctx", "rules", 1)]


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


def test_ui_select_and_options() -> None:
    menu = ui.select(
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


@pytest.mark.parametrize("custom_id", ["", "x" * 101])
def test_ui_checks_custom_id_length(custom_id: str) -> None:
    with pytest.raises(ValueError, match="custom_id must be 1-100 characters"):
        ui.button("x", custom_id=custom_id)
    with pytest.raises(ValueError, match="custom_id must be 1-100 characters"):
        ui.select(custom_id, [])


def test_ui_buttons_with_a_custom_id_exclude_link_and_premium_styles() -> None:
    style = inspect.signature(ui.button).parameters["style"].annotation

    assert get_args(style.__value__) == (
        ui.ButtonStyle.PRIMARY,
        ui.ButtonStyle.SECONDARY,
        ui.ButtonStyle.SUCCESS,
        ui.ButtonStyle.DANGER,
    )


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
