import runpy
import sys
from pathlib import Path

import pytest

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").rglob("*.py"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_every_example_builds_without_errors(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(path.parent)
    try:
        runpy.run_path(str(path), run_name="example")
    finally:
        sys.modules.pop("greetings", None)
