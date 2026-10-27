import re
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lyrecord.router import Router

MAX_CUSTOM_ID = 100
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
PATTERNS = {"": r".+?", "int": r"-?\d+"}


class ComponentRoute[**P]:
    """A component handler for a custom_id template like "vote:{tag}:{up:int}"."""

    def __init__(
        self,
        template: str,
        handler: Callable[P, Coroutine[Any, Any, None]],
        *,
        router: "Router",
    ) -> None:
        pieces = PLACEHOLDER.split(template)
        literals = pieces[::2]
        placeholders = [_placeholder(template, text) for text in pieces[1::2]]
        if any("{" in literal or "}" in literal for literal in literals):
            raise ValueError(f"{template!r} has a brace outside a placeholder")
        if not all(literals[1:-1]):
            raise ValueError(f"{template!r} needs literal text between placeholders")
        names = tuple(name for name, _ in placeholders)
        if len(set(names)) != len(names):
            raise ValueError(f"{template!r} repeats a placeholder name")
        self.template = template
        self.handler = handler
        self.router = router
        self.names = names
        self._literals = literals
        self._integers = frozenset(name for name, kind in placeholders if kind == "int")
        self._pattern = re.compile(
            re.escape(literals[0])
            + "".join(
                f"(?P<{name}>{PATTERNS[kind]})" + re.escape(literal)
                for (name, kind), literal in zip(
                    placeholders, literals[1:], strict=True
                )
            )
        )

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Coroutine[Any, Any, None]:
        return self.handler(*args, **kwargs)

    def id(self, **params: str | int) -> str:
        """Build a custom_id that fits in 100 characters and parses back to `params`."""
        if params.keys() != set(self.names):
            raise TypeError(
                f"{self.template!r} takes {sorted(self.names)}, got {sorted(params)}"
            )
        custom_id = self._literals[0] + "".join(
            str(params[name]) + literal
            for name, literal in zip(self.names, self._literals[1:], strict=True)
        )
        if len(custom_id) > MAX_CUSTOM_ID:
            raise ValueError(
                f"{custom_id!r} is {len(custom_id)} characters; Discord allows 100"
            )
        if self.match(custom_id) != params:
            raise ValueError(
                f"{custom_id!r} does not parse back to {params!r} "
                f"with the template {self.template!r}"
            )
        return custom_id

    def match(self, custom_id: str) -> dict[str, str | int] | None:
        """The placeholder values in `custom_id`, or None if it does not fit."""
        found = self._pattern.fullmatch(custom_id)
        if found is None:
            return None
        return {
            name: int(value) if name in self._integers else value
            for name, value in found.groupdict().items()
        }


def _placeholder(template: str, text: str) -> tuple[str, str]:
    name, _, kind = text.partition(":")
    if not name.isidentifier() or kind not in PATTERNS:
        raise ValueError(
            f"{template!r} has an invalid placeholder {{{text}}}; "
            "use {name} or {name:int}"
        )
    return name, kind
