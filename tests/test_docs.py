import ast
import builtins
import dataclasses
import inspect
import json
import re
import runpy
import typing
from enum import Enum
from pathlib import Path
from types import NoneType, UnionType
from typing import TYPE_CHECKING, cast

import pytest

import bostie
from bostie import (
    Bot,
    ComponentContext,
    Context,
    Message,
    ModalContext,
    RESTClient,
    checks,
    events,
    testing,
    ui,
    voice,
)
from bostie.cache import Cache
from bostie.models import AllowedMentions
from bostie.rest import REST
from bostie.testing import FakeDiscord, payloads, run, running

if TYPE_CHECKING:
    from bostie.http import HTTPClient

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs"
SKILL = DOCS / "skills" / "bostie"
SNIPPETS = sorted((DOCS / "snippets").rglob("*.py"))
I18N = runpy.run_path(str(DOCS / "i18n" / "languages.py"))
FENCE = re.compile(r"^```python\n(.*?)^```", re.MULTILINE | re.DOTALL)
ANY_FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
INCLUDE = re.compile(r'--8<-- "([^"]+)"\n')
GROUP_ROW = re.compile(r"^\| `(\w+)` \| (.+) \|$", re.MULTILINE)
SETTING_ROW = re.compile(r"^\| (`\w+`(?:, `\w+`)*) \| ([^|]+) \|", re.MULTILINE)
CODE = re.compile(r"`(\w+)`")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
DESCRIPTION = re.compile(r'^description: (".*")$', re.MULTILINE)
DOTTED = re.compile(r"(?<![\w.])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
PLUGIN_NAME = re.compile(r"(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGIN_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
PUBLIC = {
    **vars(builtins),
    **vars(typing),
    "Enum": Enum,
    **vars(testing),
    **vars(checks),
    **vars(events),
    **vars(bostie),
}
ABSENT = {"ctx.send", "ctx.reply"}


def no_run(bot: Bot, token: str | None = None, **options: object) -> None:
    pass


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", SNIPPETS, ids=lambda path: path.stem)
def test_every_snippet_defines_its_bot_without_errors(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Bot, "run", no_run)
    runpy.run_path(str(path), run_name="snippet")


def skill_blocks() -> list[str]:
    return [
        block for page in SKILL.rglob("*.md") for block in FENCE.findall(text(page))
    ]


def skill_names() -> set[str]:
    prose = [ANY_FENCE.sub("", text(page)) for page in SKILL.rglob("*.md")]
    spans = [
        QUOTED.sub("", span) for page in prose for span in INLINE_CODE.findall(page)
    ]
    return {name for span in spans for name in DOTTED.findall(span)}


def hinted(hint: object) -> list[object]:
    if isinstance(hint, UnionType) or typing.get_origin(hint) is typing.Union:
        return [arg for arg in typing.get_args(hint) if arg is not NoneType]
    return [typing.get_origin(hint) or hint]


def members(owner: object, name: str) -> list[object]:
    if not isinstance(owner, type):
        found = getattr(owner, name, None)
        return [] if found is None else [found]
    static = inspect.getattr_static(owner, name, None)
    if isinstance(static, property):
        return hinted(typing.get_type_hints(static.fget, localns=PUBLIC)["return"])
    hints = typing.get_type_hints(owner, localns=PUBLIC)
    if name in hints:
        return hinted(hints[name])
    return [] if static is None else [static]


def resolve(name: str, roots: dict[str, list[object]]) -> list[object]:
    first, *parts = name.split(".")
    owners = roots.get(first, [PUBLIC[first]] if first in PUBLIC else [])
    for part in parts:
        owners = [found for owner in owners for found in members(owner, part)]
    return owners


def test_python_blocks_include_snippets_and_every_snippet_is_shown() -> None:
    pages = [page for page in DOCS.rglob("*.md") if SKILL not in page.parents]
    blocks = [block for page in pages for block in FENCE.findall(text(page))]
    included = {ROOT / path for block in blocks for path in INCLUDE.findall(block)}
    copied = {path for path in SNIPPETS if text(path) in skill_blocks()}

    assert [block for block in blocks if not INCLUDE.fullmatch(block)] == []
    assert set(SNIPPETS) <= included | copied
    assert all(path.is_file() for path in included)


def test_every_python_block_of_the_skill_is_a_snippet() -> None:
    snippets = {text(path) for path in SNIPPETS}

    assert skill_blocks()
    assert [block for block in skill_blocks() if block not in snippets] == []


def test_every_bostie_name_the_skill_mentions_exists() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            roots: dict[str, list[object]] = {
                "bostie": [bostie],
                "bot": [bot],
                "ctx": [Context, ComponentContext, ModalContext],
                "client": [RESTClient],
                "message": [Message],
                "fake": [fake],
                "payloads": [payloads],
                "ui": [ui],
                "voice": [voice],
                "vc": [voice.VoiceConnection],
                "source": [voice.Source],
                "track": [voice.Track],
                "queue": [voice.Queue],
                "recorder": [voice.Recorder],
                "rx": [voice.Receiver],
                "sink": [voice.Sink],
            }
            names = {
                name
                for name in skill_names()
                if name.split(".")[0] in roots
                or (name[0].isupper() and not name.split(".")[0].isupper())
            }

            assert len(names) > 100
            assert [
                name for name in sorted(names - ABSENT) if not resolve(name, roots)
            ] == []
            assert [name for name in sorted(ABSENT) if resolve(name, roots)] == []

    run(scenario)


def test_the_plugin_manifests_point_at_the_skill() -> None:
    marketplace = json.loads(text(ROOT / ".claude-plugin" / "marketplace.json"))
    [entry] = marketplace["plugins"]
    plugin = ROOT / entry["source"]
    manifest = json.loads(text(plugin / "plugin.json"))

    assert marketplace["name"]
    assert marketplace["owner"]["name"]
    assert entry["source"].startswith("./")
    assert plugin / "skills" / "bostie" == SKILL
    assert manifest["$schema"] == AGENT_PLUGIN_SCHEMA
    assert manifest.keys() <= AGENT_PLUGIN_FIELDS
    assert PLUGIN_NAME.fullmatch(manifest["name"])
    assert manifest["name"] == entry["name"]
    assert text(SKILL / "SKILL.md").startswith("---\nname: bostie\n")


def test_the_readme_example_is_the_file_the_docs_include() -> None:
    readme = text(ROOT / "README.md")

    assert FENCE.findall(readme) == [
        text(DOCS / "snippets" / "home" / "readme_example.py")
    ]


def test_the_quickstart_bot_answers_its_command_on_the_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Bot, "run", no_run)
    path = DOCS / "snippets" / "quickstart" / "hello_bot.py"
    bot = runpy.run_path(str(path), run_name="snippet")["bot"]

    async def scenario(fake: FakeDiscord) -> None:
        monkeypatch.setenv("BOSTIE_API_URL", fake.api_url)
        async with running(bot, fake):
            reply = await fake.interaction(2, payloads.command("hello"))

        assert reply.json["data"]["content"].startswith("Hello, ")

    run(scenario)


def test_the_tests_of_the_testing_guide_pass() -> None:
    path = DOCS / "snippets" / "testing" / "test_greeter.py"
    tests = runpy.run_path(str(path), run_name="snippet")

    for name, test in tests.items():
        if name.startswith("test_"):
            test()


def test_a_translated_build_points_at_the_english_mascots(tmp_path: Path) -> None:
    built = tmp_path / "es"
    (built / "assets" / "mascot").mkdir(parents=True)
    (built / "assets" / "mascot" / "bostie-wave.gif").write_bytes(b"GIF")
    page = built / "guide" / "commands" / "index.html"
    page.parent.mkdir(parents=True)
    page.write_text(
        '<img src="../../assets/mascot/bostie-wave.gif">'
        '<img src="../../assets/brand/bostie-logo.svg">',
        "utf-8",
    )

    I18N["share_mascots"](built, "/")

    assert not (built / "assets" / "mascot").exists()
    assert page.read_text("utf-8") == (
        '<img src="/assets/mascot/bostie-wave.gif">'
        '<img src="../../assets/brand/bostie-logo.svg">'
    )


def test_every_registered_language_translates_every_page() -> None:
    gaps = {code: I18N["missing"](code) for code in I18N["LANGUAGES"]}

    assert {code: pages for code, pages in gaps.items() if pages} == {}


def description(page: Path) -> str | None:
    found = DESCRIPTION.findall(I18N["split"](text(page))[0])
    return cast("str", json.loads(found[0])) if len(found) == 1 else None


def test_every_page_has_a_description_of_its_own_in_every_language() -> None:
    pages = I18N["pages"](I18N["project"]()["nav"])
    folders = {"en": DOCS} | {code: DOCS / "i18n" / code for code in I18N["LANGUAGES"]}
    found = {
        code: [description(folder / page) for page in pages]
        for code, folder in folders.items()
    }

    assert {
        f"{code}/{page}"
        for code, texts in found.items()
        for page, written in zip(pages, texts, strict=True)
        if not written
    } == set()
    assert {
        code: len(texts) - len(set(texts))
        for code, texts in found.items()
        if len(set(texts)) < len(texts)
    } == {}


def test_the_event_list_names_every_event_class() -> None:
    page = text(DOCS / "reference" / "event-list.md")
    named = [
        value.__name__
        for value in vars(events).values()
        if isinstance(value, type)
        and issubclass(value, (events.Event, events.BotEvent))
        and "NAME" in vars(value)
    ]

    assert [name for name in named if f"| `{name}` |" not in page] == []


def test_the_rest_guide_lists_every_method_of_every_bot_group() -> None:
    groups = text(DOCS / "guide" / "rest.md").split("## The groups")[1]
    rows = GROUP_ROW.findall(groups)
    rest = REST(cast("HTTPClient", None), AllowedMentions(), Cache(), set())
    expected = {
        name: {
            method
            for method, _ in inspect.getmembers(type(group), inspect.isfunction)
            if not method.startswith("_")
        }
        for name, group in vars(rest).items()
    }

    assert {group: set(CODE.findall(methods)) for group, methods in rows} == expected


def test_the_voice_settings_table_gives_every_default() -> None:
    table = text(DOCS / "voice" / "index.md").split("## Settings")[1].split("\n## ")[0]
    named: dict[str, object] = {
        "EncoderSettings()": voice.EncoderSettings(),
        "MICROPHONE": voice.SpeakingFlags.MICROPHONE,
    }
    documented: dict[str, object] = {}
    for names, values in SETTING_ROW.findall(table):
        fields = CODE.findall(names)
        given = [value.strip().strip("`") for value in values.split(", ")]
        parsed = [named.get(value) or ast.literal_eval(value) for value in given]
        documented |= dict(zip(fields, parsed * len(fields), strict=False))
    defaults = voice.Defaults()

    assert documented == {
        field.name: getattr(defaults, field.name)
        for field in dataclasses.fields(defaults)
    }
