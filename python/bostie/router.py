import datetime as dt
import importlib
import inspect
import sys
import typing
from collections.abc import Callable, Coroutine, Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import ModuleType, UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    overload,
)

from bostie._util import UNSET, Unset, qualified_name, routers_from, split_subcommands
from bostie.commands import (
    Check,
    Command,
    CommandCallback,
    ContextMenuCommand,
    EntryPointCommand,
    Group,
    TargetType,
)
from bostie.custom_id import ComponentRoute
from bostie.enums import (
    ApplicationIntegrationType,
    CommandType,
    InteractionContextType,
    Permissions,
)
from bostie.errors import CommandDefinitionError, StateError
from bostie.events import BotEvent, Event, RawEvent
from bostie.hooks import ErrorHook, ErrorHooks
from bostie.models import CommandData, InteractionDataOption
from bostie.tasks import Task, TaskCallback
from bostie.voice.events import VoiceEvent

if TYPE_CHECKING:
    from bostie.bot import Bot

RAW_EVENT_NAME = RawEvent.NAME

type AnyEvent = Event | VoiceEvent | BotEvent
type ListenerCallback = Callable[[Any], Coroutine[Any, Any, None]]
type EventHandler[E: AnyEvent] = Callable[[E], Coroutine[Any, Any, None]]
type InvokeHook = Callable[[Any], Coroutine[Any, Any, None]]
type CommandKey = tuple[str, tuple[int, ...]]
type MenuKey = tuple[CommandType, str, tuple[int, ...]]


@dataclass(frozen=True, slots=True)
class Listener:
    """A registered listener; `event` is the class to decode, or None for RawEvent."""

    callback: ListenerCallback
    event: type[AnyEvent] | None


@dataclass(frozen=True, slots=True)
class _Registry[K, V]:
    of: Callable[["Router"], dict[K, V]]
    taken: Callable[[K], Exception]
    clashes: Callable[[K, Iterable[K]], bool] = lambda key, keys: key in keys


class Router(ErrorHooks):
    """Commands, listeners, routes and error hooks; a bot is a router."""

    def __init__(
        self,
        *,
        ephemeral: bool | None = None,
        auto_defer: float | Unset | None = UNSET,
        default_permissions: Permissions | None = None,
        nsfw: bool | None = None,
        contexts: Iterable[InteractionContextType] | None = None,
        integration_types: Iterable[ApplicationIntegrationType] | None = None,
    ) -> None:
        """Set what its commands and routes inherit where they set nothing."""
        super().__init__()
        self.defaults: dict[str, object] = {
            "ephemeral": ephemeral,
            "auto_defer": auto_defer,
            "default_permissions": default_permissions,
            "nsfw": nsfw,
            "contexts": contexts,
            "integration_types": integration_types,
        }
        self.parent: Router | None = None
        self.commands: dict[CommandKey, Command | Group] = {}
        self.context_menus: dict[MenuKey, ContextMenuCommand | EntryPointCommand] = {}
        self.listeners: dict[str, list[Listener]] = {}
        self.component_routes: dict[str, ComponentRoute[...]] = {}
        self.modal_routes: dict[str, ComponentRoute[...]] = {}
        self.tasks: dict[TaskCallback, Task] = {}
        self.checks: list[Check] = []
        self.before_hooks: list[InvokeHook] = []
        self.after_hooks: list[InvokeHook] = []

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
        self,
        callback: None = None,
        /,
        *,
        name: str | None = None,
        description: str | None = None,
        name_localizations: Mapping[str, str] | None = None,
        description_localizations: Mapping[str, str] | None = None,
        ephemeral: bool | None = None,
        auto_defer: float | Unset | None = UNSET,
        default_permissions: Permissions | None = None,
        nsfw: bool | None = None,
        guilds: Iterable[int] = (),
        contexts: Iterable[InteractionContextType] | None = None,
        integration_types: Iterable[ApplicationIntegrationType] | None = None,
        checks: Iterable[Check] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> Callable[[CommandCallback], Command]: ...

    def command(
        self,
        callback: CommandCallback | None = None,
        /,
        *,
        name: str | None = None,
        description: str | None = None,
        name_localizations: Mapping[str, str] | None = None,
        description_localizations: Mapping[str, str] | None = None,
        ephemeral: bool | None = None,
        auto_defer: float | Unset | None = UNSET,
        default_permissions: Permissions | None = None,
        nsfw: bool | None = None,
        guilds: Iterable[int] = (),
        contexts: Iterable[InteractionContextType] | None = None,
        integration_types: Iterable[ApplicationIntegrationType] | None = None,
        checks: Iterable[Check] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> Command | Callable[[CommandCallback], Command]:
        """Register a slash command, as `@router.command` or `@router.command(...)`."""
        settings = {
            "name": name,
            "description": description,
            "name_localizations": name_localizations,
            "description_localizations": description_localizations,
            "ephemeral": ephemeral,
            "auto_defer": auto_defer,
            "default_permissions": default_permissions,
            "nsfw": nsfw,
            "guilds": guilds,
            "contexts": contexts,
            "integration_types": integration_types,
            "checks": checks,
            "extra": extra,
        }

        def register(callback: CommandCallback) -> Command:
            command = Command(callback, router=self, settings=settings)
            self._add(COMMANDS, _scope_key(command.name, command.guilds), command)
            return command

        return register if callback is None else register(callback)

    def group(
        self,
        name: str,
        description: str,
        *,
        name_localizations: Mapping[str, str] | None = None,
        description_localizations: Mapping[str, str] | None = None,
        default_permissions: Permissions | None = None,
        nsfw: bool | None = None,
        guilds: Iterable[int] = (),
        contexts: Iterable[InteractionContextType] | None = None,
        integration_types: Iterable[ApplicationIntegrationType] | None = None,
        checks: Iterable[Check] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> Group:
        """Register a slash command made of subcommands."""
        group = Group(
            name,
            description,
            router=self,
            settings={
                "name_localizations": name_localizations,
                "description_localizations": description_localizations,
                "default_permissions": default_permissions,
                "nsfw": nsfw,
                "guilds": guilds,
                "contexts": contexts,
                "integration_types": integration_types,
                "checks": checks,
                "extra": extra,
            },
        )
        self._add(COMMANDS, _scope_key(group.name, group.guilds), group)
        return group

    def user_command(
        self,
        name: str,
        *,
        name_localizations: Mapping[str, str] | None = None,
        ephemeral: bool | None = None,
        auto_defer: float | Unset | None = UNSET,
        default_permissions: Permissions | None = None,
        nsfw: bool | None = None,
        guilds: Iterable[int] = (),
        contexts: Iterable[InteractionContextType] | None = None,
        integration_types: Iterable[ApplicationIntegrationType] | None = None,
        checks: Iterable[Check] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> Callable[[CommandCallback], ContextMenuCommand]:
        """Register `async def f(ctx, user: User)` in the context menu of users."""
        return self._context_menu(
            CommandType.USER,
            name,
            {
                "name_localizations": name_localizations,
                "ephemeral": ephemeral,
                "auto_defer": auto_defer,
                "default_permissions": default_permissions,
                "nsfw": nsfw,
                "guilds": guilds,
                "contexts": contexts,
                "integration_types": integration_types,
                "checks": checks,
                "extra": extra,
            },
        )

    def message_command(
        self,
        name: str,
        *,
        name_localizations: Mapping[str, str] | None = None,
        ephemeral: bool | None = None,
        auto_defer: float | Unset | None = UNSET,
        default_permissions: Permissions | None = None,
        nsfw: bool | None = None,
        guilds: Iterable[int] = (),
        contexts: Iterable[InteractionContextType] | None = None,
        integration_types: Iterable[ApplicationIntegrationType] | None = None,
        checks: Iterable[Check] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> Callable[[CommandCallback], ContextMenuCommand]:
        """Register `async def f(ctx, message: Message)` in messages' context menu."""
        return self._context_menu(
            CommandType.MESSAGE,
            name,
            {
                "name_localizations": name_localizations,
                "ephemeral": ephemeral,
                "auto_defer": auto_defer,
                "default_permissions": default_permissions,
                "nsfw": nsfw,
                "guilds": guilds,
                "contexts": contexts,
                "integration_types": integration_types,
                "checks": checks,
                "extra": extra,
            },
        )

    def entry_point(
        self,
        name: str,
        *,
        description: str | None = None,
        name_localizations: Mapping[str, str] | None = None,
        description_localizations: Mapping[str, str] | None = None,
        ephemeral: bool | None = None,
        auto_defer: float | Unset | None = UNSET,
        default_permissions: Permissions | None = None,
        nsfw: bool | None = None,
        contexts: Iterable[InteractionContextType] | None = None,
        integration_types: Iterable[ApplicationIntegrationType] | None = None,
        checks: Iterable[Check] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> Callable[[CommandCallback], EntryPointCommand]:
        """Register `async def f(ctx)` as the entry point that opens the app's Activity.

        The app answers it, with `ctx.launch_activity()` or otherwise.
        """
        settings = {
            "description": description,
            "name_localizations": name_localizations,
            "description_localizations": description_localizations,
            "ephemeral": ephemeral,
            "auto_defer": auto_defer,
            "default_permissions": default_permissions,
            "nsfw": nsfw,
            "contexts": contexts,
            "integration_types": integration_types,
            "checks": checks,
            "extra": extra,
        }

        def register(callback: CommandCallback) -> EntryPointCommand:
            command = EntryPointCommand(name, callback, router=self, settings=settings)
            self._add(CONTEXT_MENUS, (command.type, name, ()), command)
            return command

        return register

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
        for router in routers_from(self):
            for name, listeners in list(router.listeners.items()):
                kept = [kept for kept in listeners if kept.callback != listener]
                if kept:
                    router.listeners[name] = kept
                else:
                    del router.listeners[name]
        self._registered()

    def listen_raw[L: ListenerCallback](self, *names: str) -> Callable[[L], L]:
        """Register a listener that gets a RawEvent for each of these dispatch names."""

        def register(listener: L) -> L:
            for name in names:
                self._add_listener(name, Listener(listener, None))
            return listener

        return register

    def on_component[**P](
        self,
        template: str,
        *,
        ephemeral: bool | None = None,
        auto_defer: float | Unset | None = UNSET,
        checks: Iterable[Check] = (),
    ) -> Callable[[Callable[P, Coroutine[Any, Any, None]]], ComponentRoute[P]]:
        """Route buttons and selects whose custom_id fits `template`."""
        settings = {"ephemeral": ephemeral, "auto_defer": auto_defer, "checks": checks}
        return self._custom_id_route(COMPONENT_ROUTES, template, settings)

    def on_modal[**P](
        self,
        template: str,
        *,
        ephemeral: bool | None = None,
        auto_defer: float | Unset | None = UNSET,
        checks: Iterable[Check] = (),
    ) -> Callable[[Callable[P, Coroutine[Any, Any, None]]], ComponentRoute[P]]:
        """Route the submits of modals whose custom_id fits `template`."""
        settings = {"ephemeral": ephemeral, "auto_defer": auto_defer, "checks": checks}
        return self._custom_id_route(MODAL_ROUTES, template, settings)

    def every(
        self,
        *,
        seconds: float | None = None,
        time: dt.time | Iterable[dt.time] = (),
    ) -> Callable[[TaskCallback], Task]:
        """Run `async def task()` while the bot is ready, every `seconds` or daily.

        `time` names the times of day; one without tzinfo is in UTC. The decorator
        returns the `Task`, which starts, stops and reschedules it.
        """

        def register(callback: TaskCallback) -> Task:
            task = Task(
                callback,
                seconds=seconds,
                times=time,
                router=self,
                changed=self._registered,
            )
            self._add(TASKS, task.callback, task)
            return task

        return register

    def check[C: Check](self, predicate: C) -> C:
        """Register a check that runs before every handler of this router.

        A check takes the context and returns or awaits a bool; False refuses the
        run with CheckFailure, and it may raise a CheckFailure of its own.
        """
        self.checks.append(predicate)
        return predicate

    def before_invoke[H: InvokeHook](self, hook: H) -> H:
        """Register `async def hook(ctx)`, run after the checks of each handler here."""
        self.before_hooks.append(hook)
        return hook

    def after_invoke[H: InvokeHook](self, hook: H) -> H:
        """Register `async def hook(ctx)`, run after each handler here, even failed."""
        self.after_hooks.append(hook)
        return hook

    def include(self, router: "Router | str") -> None:
        """Add a router's commands, listeners, routes and tasks, including later ones.

        A str names a module to import, whose `router` is added.
        """
        if isinstance(router, str):
            router = _module_router(importlib.import_module(router))
        if router.parent is not None or router in routers_from(self):
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
        for ancestor in routers_from(self):
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
        self._registered()
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

    def match_command(
        self, data: CommandData
    ) -> tuple[Command, tuple[InteractionDataOption, ...]] | None:
        """The slash command `data` invokes, with its own options, or None."""
        if data.type is not CommandType.CHAT_INPUT:
            return None
        names, options = split_subcommands(data.options)
        node: Command | Group | None = scoped(
            self.commands, (data.name,), data.guild_id
        )
        for name in names:
            node = node.commands.get(name) if isinstance(node, Group) else None
        return (node, options) if isinstance(node, Command) else None

    def find_error_handlers(self, error: BaseException) -> Iterator[ErrorHook[Any]]:
        """Hooks for `error`: this router's nearest match first, then each parent's."""
        for router in routers_from(self):
            handler = router.error_handler(error)
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

    def _context_menu(
        self, type: TargetType, name: str, settings: Mapping[str, object]
    ) -> Callable[[CommandCallback], ContextMenuCommand]:
        def register(callback: CommandCallback) -> ContextMenuCommand:
            command = ContextMenuCommand(
                type, name, callback, router=self, settings=settings
            )
            self._add(CONTEXT_MENUS, (type, *_scope_key(name, command.guilds)), command)
            return command

        return register

    def _custom_id_route[**P](
        self,
        registry: _Registry[str, ComponentRoute[...]],
        template: str,
        settings: Mapping[str, object],
    ) -> Callable[[Callable[P, Coroutine[Any, Any, None]]], ComponentRoute[P]]:
        def register(
            handler: Callable[P, Coroutine[Any, Any, None]],
        ) -> ComponentRoute[P]:
            route = ComponentRoute(template, handler, router=self, settings=settings)
            self._add(registry, template, route)
            return route

        return register

    def _check[K](self, registry: _Registry[K, Any], key: K) -> None:
        if any(
            registry.clashes(key, registry.of(router)) for router in routers_from(self)
        ):
            raise registry.taken(key)

    def _add[K](self, registry: _Registry[K, Any], key: K, value: object) -> None:
        self._check(registry, key)
        for router in routers_from(self):
            registry.of(router)[key] = value
        self._registered()

    def _add_listener(self, name: str, listener: Listener) -> None:
        for router in routers_from(self):
            router.listeners.setdefault(name, []).append(listener)
        self._registered(listener)

    def _registered(self, listener: Listener | None = None) -> None:
        if self.parent is not None:
            parent: Router = self.parent
            parent._registered(listener)


COMMANDS: _Registry[CommandKey, Command | Group] = _Registry(
    lambda router: router.commands,
    lambda key: CommandDefinitionError(
        f"a command named {key[0]!r} is already registered {_where(key[1])}"
    ),
    lambda key, keys: any(_overlap(key, other) for other in keys),
)
CONTEXT_MENUS: _Registry[MenuKey, ContextMenuCommand | EntryPointCommand] = _Registry(
    lambda router: router.context_menus,
    lambda key: CommandDefinitionError(
        f"a {key[0].name.lower()} command named {key[1]!r} is already registered "
        f"{_where(key[2])}"
    ),
    lambda key, keys: any(
        key[0] == other[0] and _overlap(key[1:], other[1:]) for other in keys
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
    lambda callback: ValueError(f"{qualified_name(callback)} already runs as a task"),
)
REGISTRIES: tuple[_Registry[Any, Any], ...] = (
    COMMANDS,
    CONTEXT_MENUS,
    COMPONENT_ROUTES,
    MODAL_ROUTES,
    TASKS,
)


def scoped[V](
    registered: Mapping[tuple[Any, ...], V], key: tuple[Any, ...], guild_id: int | None
) -> V | None:
    """The command registered under `key` for `guild_id`, else the global one."""
    found = None
    for (*named, guilds), node in registered.items():
        if tuple(named) != key:
            continue
        if guild_id in guilds:
            return node
        if not guilds:
            found = node
    return found


def _scope_key(name: str, guilds: Iterable[int]) -> CommandKey:
    return name, tuple(sorted(set(guilds)))


def _overlap(key: CommandKey, other: CommandKey) -> bool:
    return key[0] == other[0] and (
        key[1] == other[1] or not set(key[1]).isdisjoint(other[1])
    )


def _where(guilds: tuple[int, ...]) -> str:
    match guilds:
        case ():
            return "globally"
        case (guild_id,):
            return f"in guild {guild_id}"
        case _:
            return f"in one of the guilds {list(guilds)}"


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
