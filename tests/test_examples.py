import runpy
import sys
from pathlib import Path

import pytest

from bostie.testing import FakeDiscord, payloads, run, running

FOLDER = Path(__file__).parent.parent / "examples"
EXAMPLES = sorted(FOLDER.rglob("*.py"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_every_example_builds_without_errors(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(path.parent)
    try:
        runpy.run_path(str(path), run_name="example")
    finally:
        sys.modules.pop("greetings", None)


def test_a_module_level_bot_runs_on_the_fake_through_bostie_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = runpy.run_path(str(FOLDER / "commands.py"), run_name="example")["bot"]

    async def scenario(fake: FakeDiscord) -> None:
        monkeypatch.setenv("BOSTIE_API_URL", fake.api_url)
        async with running(bot, fake):
            reply = await fake.interaction(
                2, payloads.command("convert", payloads.option("kilometres", 10, 10))
            )
            assert reply.json["data"]["content"] == "6.21 mi"

    run(scenario)
