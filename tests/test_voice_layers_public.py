import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from bostie.voice import layers

RUNTIME_MODULES = {
    "bostie.enums",
    "bostie.errors",
    "bostie.voice.encoder",
    "bostie.voice.formats",
}
PACKAGE = "bostie.voice"


def is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def runtime_nodes(node: ast.AST) -> Iterator[ast.AST]:
    yield node
    if isinstance(node, ast.If) and is_type_checking(node.test):
        children: list[ast.AST] = list(node.orelse)
    else:
        children = list(ast.iter_child_nodes(node))
    for child in children:
        yield from runtime_nodes(child)


def imports(node: ast.AST) -> list[tuple[str, ...]]:
    if isinstance(node, ast.Import):
        return [(alias.name,) for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    base = node.module or ""
    if node.level:
        parent = PACKAGE.rsplit(".", node.level - 1)[0]
        base = f"{parent}.{base}" if base else parent
    return [(base, f"{base}.{alias.name}") for alias in node.names]


def violations(source: str) -> list[str]:
    found: list[str] = []
    tree = ast.parse(source)
    for node in runtime_nodes(tree):
        for names in imports(node):
            bostie = names[0] == "bostie" or names[0].startswith("bostie.")
            if bostie and not set(names) & RUNTIME_MODULES:
                found.append(names[-1])
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and not (isinstance(node.value, ast.Name) and node.value.id == "self")
        ):
            found.append(f"{ast.unparse(node)} on line {node.lineno}")
    return found


def test_layers_use_only_the_public_voice_api() -> None:
    assert violations(Path(layers.__file__).read_text()) == []


@pytest.mark.parametrize(
    "source",
    [
        "from bostie import _native",
        "import bostie._native",
        "from bostie.voice.connection import VoiceConnection",
        "from bostie.voice.sink import Sink",
        "from bostie.voice.sources import Source",
        "from . import sink",
        "from .sources import Source",
        "from bostie.voice import formats, sink",
        "def f():\n    from bostie.voice.connection import VoiceManager",
        "if TYPE_CHECKING:\n    pass\nelse:\n    from bostie.voice.sink import Sink",
        "sink._native.write(b'')",
        "track._entry",
        "self._player._entries",
    ],
)
def test_the_check_rejects_private_access(source: str) -> None:
    assert violations(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "if TYPE_CHECKING:\n    from bostie.voice.connection import VoiceConnection",
        "if typing.TYPE_CHECKING:\n    from bostie.voice.sink import Marker, Sink",
        "from bostie.voice.encoder import OpusEncoder",
        "from bostie.voice.formats import PCM, Opus",
        "from .formats import PCM",
        "import asyncio",
        "self._entries.clear()",
        "entry.sink.clear()",
    ],
)
def test_the_check_accepts_public_access(source: str) -> None:
    assert violations(source) == []
