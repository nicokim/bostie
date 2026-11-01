import asyncio
import re
from typing import Any

import msgspec
import pytest

from bostie import ChannelSelect, ChannelType, SelectDefaultValue, ui
from bostie.custom_id import ComponentRoute
from bostie.models import decode_components
from bostie.router import Router


async def handler(ctx: object, **params: object) -> None:
    pass


MANY = "{a}:{b}:{c}:{d}:{e}:{f}:end"


def route(template: str) -> ComponentRoute[...]:
    return ComponentRoute(template, handler, router=Router())


@pytest.mark.parametrize(
    ("template", "custom_id", "expected"),
    [
        ("a.b+c", "a.b+c", {}),
        ("a.b+c", "axb+c", None),
        ("a.b+c", "a.b+cd", None),
        ("x:{a}", "x:abc", {"a": "abc"}),
        ("x:{a}", "x:a\nb", None),
        ("{a}-end", "a-end-end", {"a": "a-end"}),
        ("{a}-end", "abc-endx", None),
        ("vote:{tag}:{up:int}", "vote:a:b:1", {"tag": "a:b", "up": 1}),
        ("vote:{tag}:{up:int}", "vote:x:-5", {"tag": "x", "up": -5}),
        ("vote:{tag}:{up:int}", "vote:x:007", {"tag": "x", "up": 7}),
        ("vote:{tag}:{up:int}", "vote:x:-", None),
        ("vote:{tag}:{up:int}", "vote:x:y", None),
        ("vote:{tag}:{up:int}", "vote::1", None),
        ("vote:{tag}:{up:int}", "other:x:1", None),
        ("v:{n:int}", "v:12junk", None),
        ("{n:int}1{x}", "12121x", {"n": 1212, "x": "x"}),
        (MANY, "a:b:c:d:e:f:g\n:end", None),
        (
            MANY,
            "a:b:c:d:e:f:g:end",
            {"a": "a", "b": "b", "c": "c", "d": "d", "e": "e", "f": "f:g"},
        ),
    ],
)
def test_match(
    template: str, custom_id: str, expected: dict[str, str | int] | None
) -> None:
    assert route(template).match(custom_id) == expected


class Bounded(str):
    checks = 0
    limit = 0

    def startswith(self, *args: Any) -> bool:
        self.checks += 1
        assert self.checks <= self.limit
        return super().startswith(*args)


def test_a_near_miss_on_many_placeholders_takes_polynomial_steps() -> None:
    near_miss = Bounded(":" * 100)
    near_miss.limit = 6 * 100**2

    assert route(MANY).match(near_miss) is None


@pytest.mark.parametrize(
    "template",
    ["{a}{b}", "x:{a b}", "x:{a:float}", "x{", "x}", "{a}:{a}"],
)
def test_invalid_templates(template: str) -> None:
    with pytest.raises(ValueError, match=re.escape(repr(template))):
        route(template)


@pytest.mark.parametrize(
    ("params", "custom_id"),
    [
        ({"tag": "rules", "up": 1}, "vote:rules:1"),
        ({"tag": "a:b", "up": 0}, "vote:a:b:0"),
        ({"tag": "x" * 93, "up": 1}, f"vote:{'x' * 93}:1"),
        ({"tag": "x" * 92, "up": -5}, f"vote:{'x' * 92}:-5"),
    ],
)
def test_id_round_trips(params: dict[str, str | int], custom_id: str) -> None:
    vote = route("vote:{tag}:{up:int}")

    assert vote.id(**params) == custom_id
    assert vote.match(custom_id) == params


@pytest.mark.parametrize(
    ("params", "error", "problem"),
    [
        ({"tag": "x"}, TypeError, "takes \\['tag', 'up'\\]"),
        ({"tag": "x", "up": 1, "extra": 2}, TypeError, "takes"),
        ({"tag": "x" * 94, "up": 1}, ValueError, "101 characters; Discord allows 100"),
        ({"tag": "", "up": 1}, ValueError, "does not parse back"),
        ({"tag": "x\nb", "up": 1}, ValueError, "does not parse back"),
        ({"tag": "x", "up": "1"}, ValueError, "does not parse back"),
        ({"tag": "x", "up": True}, ValueError, "does not parse back"),
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
