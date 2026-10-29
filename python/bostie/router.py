import inspect
import typing
from collections.abc import Callable, Coroutine, Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import EllipsisType, UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Protocol,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    overload,
)

from bostie.commands import Command, CommandCallback, Group
from bostie.custom_id import ComponentRoute
from bostie.enums import Permissions
from bostie.errors import CommandDefinitionError

if TYPE_CHECKING:
    from bostie.bot import Bot

RAW_EVENT_NAME = "*"

type ListenerCallback = Callable[[Any], Coroutine[Any, Any, None]]
type ErrorHandler = Callable[[Any, Any], Coroutine[Any, Any, None]]


class EventClass(Protocol):
    NAME: ClassVar[str]


@dataclass(frozen=True, slots=True)
class Listener:
    """A registered listener; `event` is the class to decode, or None for RawEvent."""

    callback: ListenerCallback
    event: type[EventClass] | None


class Router:
    """Commands, listeners, component routes and error hooks; a bot is a router."""

    def __init__(self) -> None:
        self.parent: Router | None = None
        self.commands: dict[str, Command | Group] = {}
        self.listeners: dict[str, list[Listener]] = {}
        self.component_routes: dict[str, ComponentRoute[...]] = {}
        self.error_handlers: dict[type[BaseException], ErrorHandler] = {}

    @property
    def bot(self) -> "Bot":
        """The bot this router is included in; RuntimeError until then."""
        if self.parent is None:
            raise RuntimeError("router.bot exists once the router is included in a Bot")
        return self.parent.bot

    @overload
    def command(self, callback: CommandCallback, /) -> Command: ...

    @overload
    def command(
        self,
        callback: None = None,
        /,
        *,
        name: str | None = None,
        description: str | None = None,
        default_permissions: Permissions | None = None,
        guild_only: bool = False,
        nsfw: bool = False,
        ephemeral: bool = False,
        auto_defer: float | EllipsisType | None = ...,
        extra: Mapping[str, Any] | None = None,
    ) -> Callable[[CommandCallback], Command]: ...

    def command(
        self, callback: CommandCallback | None = None, /, **settings: Any
    ) -> Command | Callable[[CommandCallback], Command]:
        """Register a slash command, as `@router.command` or `@router.command(...)`."""

        def register(callback: CommandCallback) -> Command:
            command = Command(callback, router=self, **settings)
            self._add_command(command)
            return command

        return register if callback is None else register(callback)

    def group(
        self,
        name: str,
        description: str,
        *,
        default_permissions: Permissions | None = None,
        guild_only: bool = False,
        nsfw: bool = False,
        extra: Mapping[str, Any] | None = None,
    ) -> Group:
        """Register a slash command made of subcommands."""
        group = Group(
            name,
            description,
            router=self,
            default_permissions=default_permissions,
            guild_only=guild_only,
            nsfw=nsfw,
            extra=extra,
        )
        self._add_command(group)
        return group

    @overload
    def listen[L: ListenerCallback](self, listener: L, /) -> L: ...

    @overload
    def listen[L: ListenerCallback](
        self, *events: type[EventClass]
    ) -> Callable[[L], L]: ...

    def listen(self, *targets: Any) -> Any:
        """Register a listener for the events in its annotation, or the given ones."""
        if len(targets) == 1 and not isinstance(targets[0], type):
            return self._listen(targets[0], ())
        return lambda listener: self._listen(listener, targets)

    def listen_raw[L: ListenerCallback](self, *names: str) -> Callable[[L], L]:
        """Register a listener that gets a RawEvent for each of these dispatch names."""

        def register(listener: L) -> L:
            for name in names:
                self._add_listener(name, Listener(listener, None))
            return listener

        return register

    def on_component[**P](
        self, template: str
    ) -> Callable[[Callable[P, Coroutine[Any, Any, None]]], ComponentRoute[P]]:
        """Route buttons and selects whose custom_id fits `template`."""

        def register(
            handler: Callable[P, Coroutine[Any, Any, None]],
        ) -> ComponentRoute[P]:
            route = ComponentRoute(template, handler, router=self)
            self._add_route(route)
            return route

        return register

    @overload
    def on_error[H: ErrorHandler](self, handler: H, /) -> H: ...

    @overload
    def on_error[H: ErrorHandler](
        self, *exception_types: type[BaseException]
    ) -> Callable[[H], H]: ...

    def on_error(self, *targets: Any) -> Any:
        """Register `async def hook(ctx, error)` for this router's handler errors.

        Use `@router.on_error` for every Exception, or name the exception types.
        """
        if len(targets) == 1 and not isinstance(targets[0], type):
            return self._on_error(targets[0], (Exception,))
        for target in targets:
            if not (isinstance(target, type) and issubclass(target, BaseException)):
                raise TypeError(f"{target!r} is not an exception class")
        return lambda handler: self._on_error(handler, targets or (Exception,))

    def include(self, router: "Router") -> None:
        """Add a router's commands, listeners and routes, including later ones."""
        if router.parent is not None or router in self._chain():
            raise ValueError("a router can be included once, and never into itself")
        for name in router.commands:
            self._check_command(name)
        for template in router.component_routes:
            self._check_route(template)
        router.parent = self
        for node in router.commands.values():
            self._add_command(node)
        for name, listeners in router.listeners.items():
            for listener in listeners:
                self._add_listener(name, listener)
        for route in router.component_routes.values():
            self._add_route(route)

    def match_component(
        self, custom_id: str
    ) -> tuple[ComponentRoute[...], dict[str, str | int]] | None:
        """The route for a custom_id and its placeholder values, or None."""
        exact = self.component_routes.get(custom_id)
        if exact is not None and not exact.names:
            return exact, {}
        for route in self.component_routes.values():
            params = route.match(custom_id)
            if params is not None:
                return route, params
        return None

    def find_error_handlers(self, error: BaseException) -> Iterator[ErrorHandler]:
        """Hooks for `error`: this router's nearest match first, then each parent's."""
        for router in self._chain():
            handler = next(
                (
                    router.error_handlers[cls]
                    for cls in type(error).__mro__
                    if cls in router.error_handlers
                ),
                None,
            )
            if handler is not None:
                yield handler

    def walk_commands(self) -> Iterator[Command]:
        """Every command with a callback, subcommands included, in order."""
        return _leaves(self.commands.values())

    def _listen[L: ListenerCallback](self, listener: L, events: tuple[Any, ...]) -> L:
        for event in events or _annotated_events(listener):
            name = getattr(event, "NAME", None)
            if not isinstance(name, str):
                raise TypeError(f"{event!r} is not an event class")
            self._add_listener(
                name, Listener(listener, None if name == RAW_EVENT_NAME else event)
            )
        return listener

    def _on_error[H: ErrorHandler](
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

    def _chain(self) -> Iterator["Router"]:
        router: Router | None = self
        while router is not None:
            yield router
            router = router.parent

    def _check_command(self, name: str) -> None:
        if any(name in router.commands for router in self._chain()):
            raise CommandDefinitionError(
                f"a command named {name!r} is already registered"
            )

    def _check_route(self, template: str) -> None:
        if any(template in router.component_routes for router in self._chain()):
            raise ValueError(
                f"the component template {template!r} is already registered"
            )

    def _add_command(self, node: Command | Group) -> None:
        self._check_command(node.name)
        for router in self._chain():
            router.commands[node.name] = node
            router._registered()

    def _add_listener(self, name: str, listener: Listener) -> None:
        for router in self._chain():
            router.listeners.setdefault(name, []).append(listener)
            router._registered()

    def _add_route(self, route: ComponentRoute[...]) -> None:
        self._check_route(route.template)
        for router in self._chain():
            router.component_routes[route.template] = route
            router._registered()

    def _registered(self) -> None:
        pass


def _annotated_events(listener: Callable[..., object]) -> tuple[Any, ...]:
    parameters = list(inspect.signature(listener).parameters)
    hint = typing.get_type_hints(listener).get(parameters[0]) if parameters else None
    if hint is None:
        raise TypeError(
            f"{listener!r}: annotate the event parameter or pass the event classes "
            "to listen()"
        )
    if isinstance(hint, TypeAliasType):
        hint = hint.__value__
    return get_args(hint) if get_origin(hint) in (Union, UnionType) else (hint,)


def _leaves(nodes: Iterable[Command | Group]) -> Iterator[Command]:
    for node in nodes:
        if isinstance(node, Group):
            yield from _leaves(node.commands.values())
        else:
            yield node
