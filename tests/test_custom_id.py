import asyncio
import re
import threading

import pytest

from bostie.custom_id import ComponentRoute
from bostie.router import Router


async def handler(ctx: object, **params: object) -> None:
    pass


MANY = "{a}:{b}:{c}:{d}:{e}:{f}:end"


def route(template: str) -> ComponentRoute[...]:
    return Router().on_component(template)(handler)


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


def test_a_near_miss_on_many_placeholders_is_refused_at_once() -> None:
    results: list[object] = []
    worker = threading.Thread(
        target=lambda: results.append(route(MANY).match(":" * 400)), daemon=True
    )

    worker.start()
    worker.join(timeout=10)

    assert results == [None]


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

    vote_route = Router().on_component("vote:{tag}:{up:int}")(vote)
    asyncio.run(vote_route("ctx", "rules", up=1))

    assert seen == [("ctx", "rules", 1)]
