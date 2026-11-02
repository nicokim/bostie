import runpy
import sys
from pathlib import Path

import pytest

from bostie import Router

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").rglob("*.py"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_every_example_builds_its_bot_or_router(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(path.parent)
    try:
        names = runpy.run_path(str(path), run_name="example")
    finally:
        sys.modules.pop("greetings", None)

    assert isinstance(names.get("bot", names.get("router")), Router)
