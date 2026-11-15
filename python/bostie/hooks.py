from collections.abc import Callable, Coroutine, Mapping
from typing import TYPE_CHECKING, Any, cast, overload

from bostie._util import routers_from

if TYPE_CHECKING:
    from bostie.context import AnyContext, AutocompleteContext
    from bostie.router import Router

type ErrorContext = AnyContext | AutocompleteContext
type ErrorHook[E: BaseException] = Callable[
    [ErrorContext, E], Coroutine[Any, Any, None]
]


class ErrorHooks:
    """Error hooks by exception type, for a router, a command or a route."""

    def __init__(self) -> None:
        self.error_handlers: dict[type[BaseException], ErrorHook[Any]] = {}

    @overload
    def on_error[H: ErrorHook[Exception]](self, handler: H, /) -> H: ...

    @overload
    def on_error[E: BaseException](
        self, exception_type: type[E], /
    ) -> Callable[[ErrorHook[E]], ErrorHook[E]]: ...

    @overload
    def on_error[A: BaseException, B: BaseException](
        self, first: type[A], second: type[B], /
    ) -> Callable[[ErrorHook[A | B]], ErrorHook[A | B]]: ...

    @overload
    def on_error(
        self, *exception_types: type[BaseException]
    ) -> Callable[[ErrorHook[Any]], ErrorHook[Any]]: ...

    def on_error(self, *targets: Any) -> Any:
        """Register `async def hook(ctx, error)` for the errors of what this covers.

        Use `@x.on_error` for every Exception, or name the exception types.
        """
        if len(targets) == 1 and not isinstance(targets[0], type):
            return self._on_error(targets[0], (Exception,))
        for target in targets:
            if not (isinstance(target, type) and issubclass(target, BaseException)):
                raise TypeError(f"{target!r} is not an exception class")

        def register(handler: ErrorHook[Any]) -> ErrorHook[Any]:
            return self._on_error(handler, targets or (Exception,))

        return register

    def error_handler(self, error: BaseException) -> ErrorHook[Any] | None:
        """The hook registered for the nearest class of `error`, or None."""
        return next(
            (
                self.error_handlers[cls]
                for cls in type(error).__mro__
                if cls in self.error_handlers
            ),
            None,
        )

    def _on_error[H: ErrorHook[Any]](
        self, handler: H, exception_types: tuple[type[BaseException], ...]
    ) -> H:
        for exception_type in exception_types:
            if exception_type in self.error_handlers:
                raise ValueError(
                    f"an error hook for {exception_type.__name__} already exists"
                )
        for exception_type in exception_types:
            self.error_handlers[exception_type] = handler
        return handler


class Answering(ErrorHooks):
    """A command or route, and the answer settings it inherits from its routers."""

    def __init__(self, router: "Router", settings: Mapping[str, object]) -> None:
        super().__init__()
        self.router = router
        self._settings = settings

    @property
    def ephemeral(self) -> bool:
        """Whether answers are ephemeral by default, as set here or on a router."""
        return bool(self._inherited("ephemeral", False))

    @property
    def auto_defer(self) -> float | None:
        """Seconds before an unanswered use is deferred, as set here or above it."""
        for scope in self._scopes():
            if "auto_defer" in scope:
                return cast("float | None", scope["auto_defer"])
        return self.router.bot.auto_defer

    def _inherited(self, key: str, fallback: object) -> Any:
        for scope in self._scopes():
            if key in scope:
                return scope[key]
        return fallback

    def _scopes(self) -> list[Mapping[str, object]]:
        return [
            self._settings,
            *(router.defaults for router in routers_from(self.router)),
        ]
