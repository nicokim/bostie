import datetime as dt
import importlib
import inspect
import sys
import typing
from collections.abc import Callable, Coroutine, Iterable, Iterator
from dataclasses import dataclass
from types import ModuleType, UnionType
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
    _python_name,
)
from bostie.custom_id import ComponentRoute
from bostie.enums import CommandType
from bostie.errors import CommandDefinitionError, ErrorHook, StateError
from bostie.events import BotEvent, Event, RawEvent
from bostie.tasks import Task, TaskCallback
from bostie.voice.events import VoiceEvent

if TYPE_CHECKING:
    from bostie.bot import Bot

RAW_EVENT_NAME = RawEvent.NAME

type AnyEvent = Event | VoiceEvent | BotEvent
type ListenerCallback = Callable[[Any], Coroutine[Any, Any, None]]
type EventHandler[E: AnyEvent] = Callable[[E], Coroutine[Any, Any, None]]


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
        self.error_handlers: dict[type[BaseException], ErrorHook[Any]] = {}
        self.tasks: dict[TaskCallback, Task] = {}

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

    def remove_listener(self, listener: ListenerCallback) -> None:
        """Stop calling `listener` for every event it listens to here."""
        for router in self._chain():
            for name, listeners in list(router.listeners.items()):
                kept = [kept for kept in listeners if kept.callback != listener]
                if kept:
                    router.listeners[name] = kept
                else:
                    del router.listeners[name]
            router._registered()

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

    def every[T: TaskCallback](
        self,
        *,
        seconds: float | None = None,
        time: dt.time | Iterable[dt.time] = (),
    ) -> Callable[[T], T]:
        """Run `async def task()` while the bot is ready, every `seconds` or daily.

        `time` names the times of day; one without tzinfo is in UTC.
        """
        times = (time,) if isinstance(time, dt.time) else tuple(time)

        def register(callback: T) -> T:
            task = Task(callback, seconds, times)
            self._add(TASKS, task.callback, task)
            return callback

        return register

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
        """Register `async def hook(ctx, error)` for this router's handler errors.

        Use `@router.on_error` for every Exception, or name the exception types.
        """
        if len(targets) == 1 and not isinstance(targets[0], type):
            return self._on_error(targets[0], (Exception,))
        for target in targets:
            if not (isinstance(target, type) and issubclass(target, BaseException)):
                raise TypeError(f"{target!r} is not an exception class")

        def register(handler: ErrorHook[Any]) -> ErrorHook[Any]:
            return self._on_error(handler, targets or (Exception,))

        return register

    def include(self, router: "Router | str") -> None:
        """Add a router's commands, listeners, routes and tasks, including later ones.

        A str names a module to import, whose `router` is added.
        """
        if isinstance(router, str):
            router = _module_router(importlib.import_module(router))
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

    def exclude(self, router: "Router | str") -> None:
        """Remove an included router and all it added; a str names its module.

        Python forgets an excluded module, so including it again imports it anew.
        """
        name = router if isinstance(router, str) else None
        if isinstance(router, str):
            router = _imported_router(router)
        if router.parent is not self:
            raise ValueError("the router is not included here")
        removed = {
            id(listener) for kept in router.listeners.values() for listener in kept
        }
        for ancestor in self._chain():
            for registry in REGISTRIES:
                owned = registry.of(ancestor)
                for key, value in registry.of(router).items():
                    if owned.get(key) is value:
                        del owned[key]
            for event, listeners in list(ancestor.listeners.items()):
                kept = [
                    listener for listener in listeners if id(listener) not in removed
                ]
                if kept:
                    ancestor.listeners[event] = kept
                else:
                    del ancestor.listeners[event]
            ancestor._registered()
        router.parent = None
        if name is not None:
            for module in _submodules(name):
                del sys.modules[module]

    def reload(self, name: str) -> None:
        """Exclude a module's router, import the module anew and include its router.

        When that fails, the old router stays included and the error is raised.
        """
        old = _imported_router(name)
        modules = {module: sys.modules[module] for module in _submodules(name)}
        self.exclude(name)
        try:
            self.include(name)
        except BaseException:
            sys.modules.update(modules)
            self.include(old)
            raise

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

    def find_error_handlers(self, error: BaseException) -> Iterator[ErrorHook[Any]]:
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
            if not (
                isinstance(event, type)
                and issubclass(event, (Event, VoiceEvent, BotEvent))
            ):
                raise TypeError(f"{event!r} is not an event class")
            self._add_listener(
                event.NAME,
                Listener(listener, None if event.NAME == RAW_EVENT_NAME else event),
            )
        return listener

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
            router._registered(listener)

    def _registered(self, listener: Listener | None = None) -> None:
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
TASKS: _Registry[TaskCallback, Task] = _Registry(
    lambda router: router.tasks,
    lambda callback: ValueError(f"{_python_name(callback)} already runs as a task"),
)
REGISTRIES: tuple[_Registry[Any, Any], ...] = (
    COMMANDS,
    CONTEXT_MENUS,
    COMPONENT_ROUTES,
    MODAL_ROUTES,
    TASKS,
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


def _module_router(module: ModuleType) -> Router:
    router = getattr(module, "router", None)
    if not isinstance(router, Router):
        raise TypeError(f"module {module.__name__!r} has no Router named `router`")
    return router


def _imported_router(name: str) -> Router:
    module = sys.modules.get(name)
    if module is None:
        raise ValueError(f"module {name!r} is not imported")
    return _module_router(module)


def _submodules(name: str) -> list[str]:
    return [
        module
        for module in list(sys.modules)
        if module == name or module.startswith(f"{name}.")
    ]


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
