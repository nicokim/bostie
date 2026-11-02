from collections.abc import Callable
from typing import Any, overload

from bostie.errors import ErrorHook


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
