import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from bostie._aio import spawn
from bostie._util import UNSET, Unset, qualified_name, routers_from
from bostie.checks import ReleasingCheck
from bostie.commands import (
    AutocompleteCallback,
    Check,
    Command,
    ContextMenuCommand,
    EntryPointCommand,
    Group,
    autocomplete_choices,
)
from bostie.context import (
    AnyContext,
    AutocompleteContext,
    ComponentContext,
    Context,
    ModalContext,
    _ResponseState,
)
from bostie.custom_id import ComponentRoute
from bostie.errors import CheckFailure, InteractionResponded
from bostie.events import InteractionCreate
from bostie.hooks import ErrorContext
from bostie.models import (
    AllowedMentions,
    AutocompleteInteraction,
    CommandInteraction,
    ComponentInteraction,
    Interaction,
    InteractionDataOption,
    ModalSubmitInteraction,
    UnknownInteraction,
)
from bostie.router import Router, _scoped
from bostie.snowflake import snowflake_time

if TYPE_CHECKING:
    from bostie.bot import Bot

log = logging.getLogger("bostie.bot")

STALE_INTERACTION = 5.0
SLOW_HANDLER_START = 1.0
FAILURE_ANSWER = "Something went wrong."
UNANSWERED = frozenset(
    {_ResponseState.PENDING, _ResponseState.DEFERRED, _ResponseState.DEFERRED_UPDATE}
)
CLAIMED_INTERACTIONS: dict[
    type, type[ComponentInteraction | ModalSubmitInteraction]
] = {
    ComponentContext: ComponentInteraction,
    ModalContext: ModalSubmitInteraction,
}

type Handler = Callable[..., Coroutine[Any, Any, None]]
type Node = Command | ContextMenuCommand | EntryPointCommand | ComponentRoute[...]
type Arguments = Callable[[], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


@dataclass(slots=True)
class Claim[C: ComponentContext | ModalContext]:
    """A `wait_for_component` or `wait_for_modal` waiting for its interaction."""

    context: type[C]
    custom_ids: frozenset[str]
    check: Callable[[C], bool] | None
    ephemeral: bool
    auto_defer: float | Unset | None
    future: asyncio.Future[C]


class Interactions:
    """Runs each interaction's claim or handler, with its checks, hooks and errors."""

    def __init__(
        self,
        bot: "Bot",
        *,
        tasks: set[asyncio.Task[None]],
        replaying: set[int],
        allowed_mentions: AllowedMentions,
        deletions: set[asyncio.Task[None]],
    ) -> None:
        self.claims: list[Claim[Any]] = []
        self._bot = bot
        self._tasks = tasks
        self._replaying = replaying
        self._allowed_mentions = allowed_mentions
        self._deletions = deletions

    def route(self, interaction: Interaction, received: float, shard_id: int) -> None:
        """Run what `interaction` reaches, unless its token died during a replay."""
        anchor = received
        if shard_id in self._replaying:
            age = (datetime.now(UTC) - snowflake_time(interaction.id)).total_seconds()
            if age > STALE_INTERACTION:
                log.warning(
                    "interaction %d was replayed %.1f s after it was created; "
                    "its token is dead, so no handler runs",
                    interaction.id,
                    age,
                )
                return
            anchor = min(received, asyncio.get_running_loop().time() - age)
        if self._claimed(interaction, anchor):
            return
        handler = self._handler(interaction, anchor)
        if handler is None:
            log.log(
                logging.DEBUG
                if InteractionCreate.NAME in self._bot.listeners
                else logging.WARNING,
                "nothing handles interaction %d (%s); Discord shows "
                "'The application did not respond'",
                interaction.id,
                _described(interaction),
            )
            return
        delay = asyncio.get_running_loop().time() - received
        if delay > SLOW_HANDLER_START:
            log.warning(
                "interaction %d reached its handler %.1f s after it was received; "
                "something is blocking the event loop",
                interaction.id,
                delay,
            )
        spawn(self._tasks, handler, eager=True)

    def _claimed(self, interaction: Interaction, received: float) -> bool:
        for claim in self.claims:
            if claim.future.done() or not isinstance(
                interaction, CLAIMED_INTERACTIONS[claim.context]
            ):
                continue
            if claim.custom_ids and interaction.data.custom_id not in claim.custom_ids:
                continue
            ctx = claim.context(
                interaction,
                self._bot,
                received=received,
                ephemeral=claim.ephemeral,
                allowed_mentions=self._allowed_mentions,
                deletions=self._deletions,
            )
            try:
                matched = claim.check is None or claim.check(ctx)
            except Exception as error:
                claim.future.set_exception(error)
                continue
            if matched:
                self._defer_when_slow(
                    ctx,
                    self._bot.auto_defer
                    if claim.auto_defer is UNSET
                    else claim.auto_defer,
                )
                claim.future.set_result(ctx)
                return True
        return False

    def _handler(
        self, interaction: Interaction, received: float
    ) -> Coroutine[Any, Any, None] | None:
        match interaction:
            case ComponentInteraction():
                return self._route_handler(
                    self._bot.match_component(interaction.data.custom_id),
                    partial(ComponentContext, interaction),
                    received,
                )
            case ModalSubmitInteraction():
                return self._route_handler(
                    self._bot.match_modal(interaction.data.custom_id),
                    partial(ModalContext, interaction),
                    received,
                )
            case CommandInteraction():
                return self._command_handler(interaction, received)
            case AutocompleteInteraction():
                return self._autocomplete_handler(interaction, received)
            case UnknownInteraction():
                return None

    def _route_handler(
        self,
        found: tuple[ComponentRoute[...], dict[str, str | int]] | None,
        context: Callable[..., ComponentContext | ModalContext],
        received: float,
    ) -> Coroutine[Any, Any, None] | None:
        if found is None:
            return None
        route, params = found
        ctx = context(
            self._bot,
            received=received,
            ephemeral=route.ephemeral,
            allowed_mentions=self._allowed_mentions,
            deletions=self._deletions,
        )
        return self._run_handler(ctx, route, route.handler, params.copy)

    def _command_handler(
        self, interaction: CommandInteraction, received: float
    ) -> Coroutine[Any, Any, None] | None:
        data = interaction.data
        command: Command | ContextMenuCommand | EntryPointCommand
        options: tuple[InteractionDataOption, ...] = ()
        found = self._bot._match_command(data)
        if found is not None:
            command, options = found
        elif (
            menu := _scoped(
                self._bot.context_menus, (data.type, data.name), data.guild_id
            )
        ) is not None:
            command = menu
        else:
            return None
        ctx = Context(
            interaction,
            self._bot,
            received=received,
            ephemeral=command.ephemeral,
            allowed_mentions=self._allowed_mentions,
            deletions=self._deletions,
        )
        arguments: Arguments = dict
        if isinstance(command, Command):
            arguments = partial(command._arguments, ctx, options, data.resolved)
        elif isinstance(command, ContextMenuCommand):
            arguments = partial(command._bind, interaction)
        return self._run_handler(ctx, command, command.callback, arguments)

    def _autocomplete_handler(
        self, interaction: AutocompleteInteraction, received: float
    ) -> Coroutine[Any, Any, None] | None:
        found = self._bot._match_command(interaction.data)
        if found is None:
            return None
        command, options = found
        focused = next((option for option in options if option.focused), None)
        function = None if focused is None else command._autocompletes.get(focused.name)
        if focused is None or function is None:
            return None
        ctx = AutocompleteContext(interaction, self._bot, received=received)
        return self._autocomplete(ctx, command, function, focused)

    async def _run_handler(
        self,
        ctx: AnyContext,
        node: Node,
        handler: Handler,
        arguments: Arguments,
    ) -> None:
        passed: list[Check] = []
        routers = [*routers_from(node.router)][::-1]
        timer = self._defer_when_slow(ctx, node.auto_defer)
        try:
            for check in _checks(node, routers):
                await _run_check(check, ctx)
                passed.append(check)
            for router in routers:
                for hook in router._before_hooks:
                    await hook(ctx)
            try:
                bound = arguments()
                if inspect.isawaitable(bound):
                    bound = await bound
                await handler(ctx, **bound)
            finally:
                await _after(ctx, routers)
        except Exception as error:
            await self._handle_error(ctx, node, handler, error)
            return
        finally:
            if timer is not None:
                timer.cancel()
            for check in passed:
                if isinstance(check, ReleasingCheck):
                    check.release(ctx)
        if ctx._state is _ResponseState.PENDING:
            log.warning(
                "%s returned without answering; Discord shows "
                "'The application did not respond'",
                qualified_name(handler),
            )
        elif ctx._state is _ResponseState.DEFERRED:
            log.warning(
                "%s returned after deferring without answering; the user sees "
                "'thinking' until the interaction expires",
                qualified_name(handler),
            )

    def _defer_when_slow(
        self, ctx: AnyContext, seconds: float | None
    ) -> asyncio.TimerHandle | None:
        if seconds is None:
            return None
        return asyncio.get_running_loop().call_at(
            ctx._received + seconds, self._defer_if_unanswered, ctx
        )

    def _defer_if_unanswered(self, ctx: AnyContext) -> None:
        if ctx._state is _ResponseState.PENDING:
            spawn(self._tasks, _defer_unanswered(ctx))

    async def _autocomplete(
        self,
        ctx: AutocompleteContext,
        command: Command,
        function: AutocompleteCallback,
        focused: InteractionDataOption,
    ) -> None:
        try:
            result = await function(ctx, focused.value)
            await ctx.respond(autocomplete_choices(result, function, focused.type))
        except Exception as error:
            await self._handle_error(ctx, command, function, error)

    async def _handle_error(
        self,
        ctx: ErrorContext,
        node: Node,
        handler: Callable[..., object],
        error: Exception,
    ) -> None:
        hooks = (scope._error_handler(error) for scope in _nodes(node))
        for hook in (
            *(hook for hook in hooks if hook is not None),
            *node.router.find_error_handlers(error),
        ):
            try:
                await hook(ctx, error)
            except Exception as raised:
                if raised is error:
                    continue
                log.exception(
                    "the error hook %s raised while handling %r",
                    qualified_name(hook),
                    error,
                )
            if ctx._state in UNANSWERED:
                log.warning(
                    "the error hook %s left interaction %d unanswered; "
                    "bostie answered %r",
                    qualified_name(hook),
                    ctx.interaction.id,
                    FAILURE_ANSWER,
                )
                await _answer_failure(ctx)
            return
        if isinstance(error, CheckFailure):
            log.debug("a check refused %s: %s", qualified_name(handler), error)
            await _answer_failure(ctx, str(error))
            return
        log.error("%s raised", qualified_name(handler), exc_info=error)
        await _answer_failure(ctx)


async def _defer_unanswered(ctx: AnyContext) -> None:
    try:
        if isinstance(ctx, Context) or ctx.interaction.message_flags is None:
            await ctx.defer()
        else:
            await ctx.defer_update()
    except InteractionResponded:
        pass
    except Exception:
        log.warning(
            "automatic defer of %s failed", _described(ctx.interaction), exc_info=True
        )


async def _answer_failure(
    ctx: ErrorContext,
    answer: str = FAILURE_ANSWER,
) -> None:
    try:
        if isinstance(ctx, AutocompleteContext):
            if ctx._state is _ResponseState.PENDING:
                await ctx.respond([])
        elif ctx._state in (_ResponseState.PENDING, _ResponseState.DEFERRED_UPDATE):
            await ctx.respond(answer, ephemeral=True)
        elif ctx._state is _ResponseState.DEFERRED:
            await ctx.respond(answer)
    except Exception:
        log.exception("answering interaction %d failed", ctx.interaction.id)


def _described(interaction: Interaction) -> str:
    match interaction:
        case CommandInteraction() | AutocompleteInteraction():
            return f"command {interaction.data.name!r}"
        case ComponentInteraction() | ModalSubmitInteraction():
            return f"custom_id {interaction.data.custom_id!r}"
        case UnknownInteraction():
            return f"type {interaction.type}"


def _nodes(
    node: Node,
) -> list[Node | Group]:
    nodes: list[Node | Group] = [node]
    while isinstance(parent := nodes[-1], Command | Group) and parent.parent:
        nodes.append(parent.parent)
    return nodes


def _checks(node: Node, routers: list[Router]) -> list[Check]:
    return [
        *(check for router in routers for check in router._checks),
        *(check for scope in reversed(_nodes(node)) for check in scope._checks),
    ]


async def _after(ctx: AnyContext, routers: list[Router]) -> None:
    for router in reversed(routers):
        for hook in router._after_hooks:
            try:
                await hook(ctx)
            except Exception:
                log.exception("the after_invoke hook %s raised", qualified_name(hook))


async def _run_check(check: Check, ctx: AnyContext) -> None:
    result = check(ctx)
    if not (await result if inspect.isawaitable(result) else result):
        raise CheckFailure
