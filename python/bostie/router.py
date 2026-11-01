import inspect
import typing
from collections.abc import Callable, Coroutine, Iterable, Iterator
from dataclasses import dataclass
from types import UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    TypeAliasType,
    Union,
    Unpack,
    get_args,
    get_origin,
    overload,
)

from bostie.commands import (
    Command,
    CommandCallback,
    CommandSettings,
    ContextMenuCommand,
    ContextMenuSettings,
    Group,
    GroupSettings,
    TargetType,
)
from bostie.custom_id import ComponentRoute
from bostie.enums import CommandType
from bostie.errors import CommandDefinitionError, StateError
from bostie.events import Event, RawEvent
from bostie.voice.events import VoiceEvent

if TYPE_CHECKING:
    from bostie.bot import Bot

RAW_EVENT_NAME = RawEvent.NAME

type AnyEvent = Event | VoiceEvent
type ListenerCallback = Callable[[Any], Coroutine[Any, Any, None]]
type EventHandler[E: AnyEvent] = Callable[[E], Coroutine[Any, Any, None]]
type ErrorHandler = Callable[[Any, Any], Coroutine[Any, Any, None]]


@dataclass(frozen=True, slots=True)
class Listener:
    """A registered listener; `event` is the class to decode, or None for RawEvent."""

    callback: ListenerCallback
    event: type[AnyEvent] | None


@dataclass(frozen=True, slots=True)
class _Registry[K, V]:
    of: Callable[["Router"], dict[K, V]]
    taken: Callable[[K], Exception]


class Router:
    """Commands, listeners, routes and error hooks; a bot is a router."""

    def __init__(self) -> None:
        self.parent: Router | None = None
        self.commands: dict[str, Command | Group] = {}
        self.context_menus: dict[tuple[CommandType, str], ContextMenuCommand] = {}
        self.listeners: dict[str, list[Listener]] = {}
        self.component_routes: dict[str, ComponentRoute[...]] = {}
        self.modal_routes: dict[str, ComponentRoute[...]] = {}
        self.error_handlers: dict[type[BaseException], ErrorHandler] = {}

    @property
    def bot(self) -> "Bot":
        """The bot this router is included in; StateError until then."""
        if self.parent is None:
            raise StateError("router.bot exists once the router is included in a Bot")
        return self.parent.bot

    @overload
    def command(self, callback: CommandCallback, /) -> Command: ...

    @overload
    def command(
        self, callback: None = None, /, **settings: Unpack[CommandSettings]
    ) -> Callable[[CommandCallback], Command]: ...

    def command(
        self,
        callback: CommandCallback | None = None,
        /,
        **settings: Unpack[CommandSettings],
    ) -> Command | Callable[[CommandCallback], Command]:
        """Register a slash command, as `@router.command` or `@router.command(...)`."""

        def register(callback: CommandCallback) -> Command:
            command = Command(callback, router=self, **settings)
            self._add(COMMANDS, command.name, command)
            return command

        return register if callback is None else register(callback)

    def group(
        self, name: str, description: str, **settings: Unpack[GroupSettings]
    ) -> Group:
        """Register a slash command made of subcommands."""
        group = Group(name, description, router=self, **settings)
        self._add(COMMANDS, group.name, group)
        return group

    def user_command(
        self, name: str, **settings: Unpack[ContextMenuSettings]
    ) -> Callable[[CommandCallback], ContextMenuCommand]:
        """Register `async def f(ctx, user: User)` in the context menu of users."""
        return self._context_menu(CommandType.USER, name, settings)

    def message_command(
        self, name: str, **settings: Unpack[ContextMenuSettings]
    ) -> Callable[[CommandCallback], ContextMenuCommand]:
        """Register `async def f(ctx, message: Message)` in messages' context menu."""
        return self._context_menu(CommandType.MESSAGE, name, settings)

    @overload
    def listen[E: AnyEvent](
        self, event: type[E], /
    ) -> Callable[[EventHandler[E]], EventHandler[E]]: ...

    @overload
    def listen[A: AnyEvent, B: AnyEvent](
        self, first: type[A], second: type[B], /
    ) -> Callable[[EventHandler[A | B]], EventHandler[A | B]]: ...

    @overload
    def listen[L: ListenerCallback](
        self,
        first: type[AnyEvent],
        second: type[AnyEvent],
        third: type[AnyEvent],
        /,
        *more: type[AnyEvent],
    ) -> Callable[[L], L]: ...

    @overload
    def listen[L: ListenerCallback](self, listener: L, /) -> L: ...

    def listen(self, *targets: Any) -> Any:
        """Register a listener for the events in its annotation, or the given ones."""
        if len(targets) == 1 and not isinstance(targets[0], type):
            return self._listen(targets[0], ())

        def register(listener: ListenerCallback) -> ListenerCallback:
            return self._listen(listener, targets)

        return register

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
        return self._custom_id_route(COMPONENT_ROUTES, template)

    def on_modal[**P](
        self, template: str
    ) -> Callable[[Callable[P, Coroutine[Any, Any, None]]], ComponentRoute[P]]:
        """Route the submits of modals whose custom_id fits `template`."""
        return self._custom_id_route(MODAL_ROUTES, template)

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

        def register(handler: ErrorHandler) -> ErrorHandler:
            return self._on_error(handler, targets or (Exception,))

        return register

    def include(self, router: "Router") -> None:
        """Add a router's commands, listeners and routes, including later ones."""
        if router.parent is not None or router in self._chain():
            raise ValueError("a router can be included once, and never into itself")
        for registry in REGISTRIES:
            for key in registry.of(router):
                self._check(registry, key)
        router.parent = self
        for registry in REGISTRIES:
            for key, value in registry.of(router).items():
                self._add(registry, key, value)
        for name, listeners in router.listeners.items():
            for listener in listeners:
                self._add_listener(name, listener)

    def match_component(
        self, custom_id: str
    ) -> tuple[ComponentRoute[...], dict[str, str | int]] | None:
        """The component route for a custom_id and its placeholder values, or None."""
        return _match(self.component_routes, custom_id)

    def match_modal(
        self, custom_id: str
    ) -> tuple[ComponentRoute[...], dict[str, str | int]] | None:
        """The modal route for a custom_id and its placeholder values, or None."""
        return _match(self.modal_routes, custom_id)

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
            if not (isinstance(event, type) and issubclass(event, (Event, VoiceEvent))):
                raise TypeError(f"{event!r} is not an event class")
            self._add_listener(
                event.NAME,
                Listener(listener, None if event.NAME == RAW_EVENT_NAME else event),
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

    def _context_menu(
        self, type: TargetType, name: str, settings: ContextMenuSettings
    ) -> Callable[[CommandCallback], ContextMenuCommand]:
        def register(callback: CommandCallback) -> ContextMenuCommand:
            command = ContextMenuCommand(type, name, callback, router=self, **settings)
            self._add(CONTEXT_MENUS, (type, name), command)
            return command

        return register

    def _custom_id_route[**P](
        self, registry: _Registry[str, ComponentRoute[...]], template: str
    ) -> Callable[[Callable[P, Coroutine[Any, Any, None]]], ComponentRoute[P]]:
        def register(
            handler: Callable[P, Coroutine[Any, Any, None]],
        ) -> ComponentRoute[P]:
            route = ComponentRoute(template, handler, router=self)
            self._add(registry, template, route)
            return route

        return register

    def _check[K](self, registry: _Registry[K, Any], key: K) -> None:
        if any(key in registry.of(router) for router in self._chain()):
            raise registry.taken(key)

    def _add[K](self, registry: _Registry[K, Any], key: K, value: object) -> None:
        self._check(registry, key)
        for router in self._chain():
            registry.of(router)[key] = value
            router._registered()

    def _add_listener(self, name: str, listener: Listener) -> None:
        for router in self._chain():
            router.listeners.setdefault(name, []).append(listener)
            router._registered()

    def _registered(self) -> None:
        pass


COMMANDS: _Registry[str, Command | Group] = _Registry(
    lambda router: router.commands,
    lambda name: CommandDefinitionError(
        f"a command named {name!r} is already registered"
    ),
)
CONTEXT_MENUS: _Registry[tuple[CommandType, str], ContextMenuCommand] = _Registry(
    lambda router: router.context_menus,
    lambda key: CommandDefinitionError(
        f"a {key[0].name.lower()} command named {key[1]!r} is already registered"
    ),
)
COMPONENT_ROUTES: _Registry[str, ComponentRoute[...]] = _Registry(
    lambda router: router.component_routes,
    lambda template: ValueError(
        f"the component template {template!r} is already registered"
    ),
)
MODAL_ROUTES: _Registry[str, ComponentRoute[...]] = _Registry(
    lambda router: router.modal_routes,
    lambda template: ValueError(
        f"the modal template {template!r} is already registered"
    ),
)
REGISTRIES: tuple[_Registry[Any, Any], ...] = (
    COMMANDS,
    CONTEXT_MENUS,
    COMPONENT_ROUTES,
    MODAL_ROUTES,
)


def _match(
    routes: dict[str, ComponentRoute[...]], custom_id: str
) -> tuple[ComponentRoute[...], dict[str, str | int]] | None:
    exact = routes.get(custom_id)
    if exact is not None and not exact.names:
        return exact, {}
    for route in routes.values():
        params = route.match(custom_id)
        if params is not None:
            return route, params
    return None


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
