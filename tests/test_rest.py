import ast
import inspect
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, cast

from bostie.cache import Cache
from bostie.models import AllowedMentions
from bostie.rest import REST

if TYPE_CHECKING:
    from bostie.http import HTTPClient

FIXTURES = Path(__file__).parent / "fixtures" / "rest"
VERBS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

type Shape = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]


def rows(path: Path) -> list[list[str]]:
    lines = path.read_text().splitlines()
    return [line.split() for line in lines if not line.startswith("#")]


def pending_files() -> list[Path]:
    return sorted((FIXTURES / "pending").glob("*.txt"))


def operation(verb: str, path: str) -> tuple[str, str]:
    return verb, re.sub(r"\{\w+\}", "{}", path)


def implemented(rest: REST) -> dict[str, Shape]:
    found: dict[str, Shape] = {}
    for group_name, group in vars(rest).items():
        for name, function in vars(type(group)).items():
            if name.startswith("_") or not inspect.isfunction(function):
                continue
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
            texts = [
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            ]
            parameters = inspect.signature(function).parameters.values()
            found[f"{group_name}.{name}"] = (
                tuple(text for text in texts if text in VERBS) or ("GET",),
                tuple(text for text in texts if text.startswith("/")),
                tuple(
                    parameter.name
                    for parameter in parameters
                    if parameter.kind is parameter.POSITIONAL_ONLY
                    and parameter.name != "self"
                ),
                any(parameter.name == "reason" for parameter in parameters),
            )
    return found


def test_the_rest_groups_implement_every_table_row_not_pending() -> None:
    table = rows(FIXTURES / "operations.txt")
    pending = {row[2] for path in pending_files() for row in rows(path)}
    rest = REST(cast("HTTPClient", None), AllowedMentions(), Cache())

    groups = {name.split(".")[0] for _, _, name, *_ in table}
    assert {name: type(group).__module__ for name, group in vars(rest).items()} == {
        group: f"bostie.rest.{group}" for group in groups
    }
    assert implemented(rest) == {
        name: (
            (verb,),
            (path,),
            tuple(re.findall(r"\{(\w+)\}", path)),
            "reason" in marks,
        )
        for verb, path, name, *marks in table
        if name not in pending
    }


def test_pending_rows_are_table_rows_of_their_own_group() -> None:
    table = {tuple(row) for row in rows(FIXTURES / "operations.txt")}
    for path in pending_files():
        listed = [tuple(row) for row in rows(path)]
        assert listed
        assert len(set(listed)) == len(listed)
        assert set(listed) <= table
        assert {row[2].split(".")[0] for row in listed} == {path.stem}


def test_every_pinned_spec_operation_is_named_or_out_of_scope() -> None:
    spec = {operation(*row) for row in rows(FIXTURES / "spec.txt")}
    named = [operation(*row[:2]) for row in rows(FIXTURES / "operations.txt")]
    skipped = {operation(*row[:2]) for row in rows(FIXTURES / "out_of_scope.txt")}
    assert spec - set(named) - skipped == set()
    assert set(named) & skipped == set()
