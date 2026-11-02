import re
from collections.abc import Callable, Coroutine, Iterable
from functools import cache
from typing import TYPE_CHECKING, Any, TypedDict, Unpack

from bostie.commands import Check, _auto_defer, _inherited
from bostie.hooks import ErrorHooks
from bostie.models.base import check_keys

if TYPE_CHECKING:
    from bostie.router import Router

MAX_CUSTOM_ID = 100
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
KINDS = frozenset({"", "int"})


class AnswerSettings(TypedDict, total=False):
    """How a component or modal context answers; `auto_defer` defaults to the bot's."""

    ephemeral: bool
    auto_defer: float | None


class RouteSettings(AnswerSettings, total=False):
    """How a route answers, and the checks that run before its handler."""

    checks: Iterable[Check]


ANSWER_SETTINGS = AnswerSettings.__optional_keys__
ROUTE_SETTINGS = RouteSettings.__optional_keys__


class ComponentRoute[**P](ErrorHooks):
    """A component or modal handler for a custom_id template like "vote:{up:int}"."""

    def __init__(
        self,
        template: str,
        handler: Callable[P, Coroutine[Any, Any, None]],
        *,
        router: "Router",
        **settings: Unpack[RouteSettings],
    ) -> None:
        super().__init__()
        check_keys(settings, ROUTE_SETTINGS, "settings")
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
        self.checks = tuple(settings.get("checks", ()))
        self._settings = settings
        self.names = names
        self._literals = literals
        self._kinds = tuple(kind for _, kind in placeholders)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Coroutine[Any, Any, None]:
        return self.handler(*args, **kwargs)

    @property
    def ephemeral(self) -> bool:
        """Whether answers are ephemeral unless a call says; set here or on a router."""
        return bool(_inherited(self._settings, "ephemeral", self.router, False))

    @property
    def auto_defer(self) -> float | None:
        """Seconds before an unanswered use is deferred; the bot's unless set here."""
        return _auto_defer(self._settings, self.router)

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
        head = self._literals[0]
        if not custom_id.startswith(head):
            return None

        @cache
        def values(index: int, start: int) -> tuple[str, ...] | None:
            if index == len(self._kinds):
                return () if start == len(custom_id) else None
            literal = self._literals[index + 1]
            for end in _ends(self._kinds[index], custom_id, start):
                if custom_id.startswith(literal, end):
                    rest = values(index + 1, end + len(literal))
                    if rest is not None:
                        return (custom_id[start:end], *rest)
            return None

        found = values(0, len(head))
        if found is None:
            return None
        return {
            name: int(value) if kind == "int" else value
            for name, kind, value in zip(self.names, self._kinds, found, strict=True)
        }


def _placeholder(template: str, text: str) -> tuple[str, str]:
    name, _, kind = text.partition(":")
    if not name.isidentifier() or kind not in KINDS:
        raise ValueError(
            f"{template!r} has an invalid placeholder {{{text}}}; "
            "use {name} or {name:int}"
        )
    return name, kind


def _ends(kind: str, text: str, start: int) -> range:
    if kind == "int":
        digits = start + text.startswith("-", start)
        stop = digits
        while stop < len(text) and text[stop].isdecimal():
            stop += 1
        return range(stop, digits, -1)
    newline = text.find("\n", start)
    return range(start + 1, (len(text) if newline < 0 else newline) + 1)
