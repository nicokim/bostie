import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict, Unpack

import msgspec

from bostie.enums import (
    InteractionCallbackType,
    JSONErrorCode,
    MessageFlags,
    OptionType,
)
from bostie.errors import (
    HTTPException,
    InteractionExpired,
    InteractionNotAnswered,
    InteractionResponded,
)
from bostie.file import File
from bostie.http import decode_answer
from bostie.models import (
    AllowedMentions,
    Attachment,
    AutocompleteInteraction,
    CommandData,
    CommandInteraction,
    ComponentInteraction,
    EditParts,
    InteractionDataOption,
    Member,
    Message,
    MessageParts,
    PartialChannel,
    PartialMessage,
    User,
    WebhookMessage,
    decode,
    decode_message,
)
from bostie.models.base import check_keys, decoder
from bostie.models.message import in_guild, message_body
from bostie.voice.connection import ContextVoice

if TYPE_CHECKING:
    from bostie.bot import Bot

logger = logging.getLogger("bostie.context")

TOKEN_LIFETIME = 15 * 60
CALLBACK_ROUTE = "/interactions/{interaction_id}/{interaction_token}/callback"
ORIGINAL_ROUTE = "/webhooks/{application_id}/{interaction_token}/messages/@original"
FOLLOWUP_ROUTE = "/webhooks/{application_id}/{interaction_token}"


SUBCOMMAND_TYPES = (OptionType.SUB_COMMAND, OptionType.SUB_COMMAND_GROUP)


class ResponseState(Enum):
    PENDING = "pending"
    DEFERRED = "deferred"
    DEFERRED_UPDATE = "deferred_update"
    RESPONDED = "responded"
    EXPIRED = "expired"


class _CallbackResource(msgspec.Struct):
    message: msgspec.Raw


class _CallbackResponse(msgspec.Struct):
    resource: _CallbackResource


class _MessageFlagsOnly(msgspec.Struct):
    flags: MessageFlags = MessageFlags(0)


class ReplyParts(MessageParts, total=False):
    """The parts of an answer; without `ephemeral`, the command's setting applies."""

    ephemeral: bool


class ReplyEditParts(EditParts, total=False):
    """The parts of an edit; `ephemeral` cannot change the message and only warns."""

    ephemeral: bool


class DeferOptions(TypedDict, total=False):
    """Without `ephemeral`, the command's setting applies."""

    ephemeral: bool


REPLY_PARTS = ReplyParts.__optional_keys__
REPLY_EDIT_PARTS = ReplyEditParts.__optional_keys__
DEFER_OPTIONS = DeferOptions.__optional_keys__


class _InteractionContext[
    I: CommandInteraction | ComponentInteraction | AutocompleteInteraction
]:
    def __init__(self, interaction: I, bot: "Bot", *, received: float) -> None:
        self.interaction = interaction
        self.bot = bot
        self.received = received
        self.state = ResponseState.PENDING
        self._lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()

    @property
    def user(self) -> User:
        """The user who triggered the interaction, in a guild or in a DM."""
        return self.interaction.user

    @property
    def member(self) -> Member | None:
        return self.interaction.member

    @property
    def guild_id(self) -> int | None:
        return self.interaction.guild_id

    @property
    def channel_id(self) -> int | None:
        return self.interaction.channel_id

    @property
    def channel(self) -> PartialChannel | None:
        return self.interaction.channel

    @property
    def locale(self) -> str:
        return self.interaction.locale

    @property
    def voice(self) -> ContextVoice:
        """Voice in this guild; `connect()` joins the channel of the user who ran it."""
        return ContextVoice(self.bot.voice, self.guild_id, self.user.id)

    def _ensure_alive(self) -> None:
        if self.state is ResponseState.EXPIRED:
            raise InteractionExpired("Discord no longer accepts this interaction")
        if self._loop.time() - self.received > TOKEN_LIFETIME:
            raise InteractionExpired(
                "interactions can only be answered within 15 minutes"
            )

    async def _callback(
        self,
        type: InteractionCallbackType,
        data: Mapping[str, Any] | None = None,
        files: Sequence[File] = (),
    ) -> bytes:
        body: dict[str, Any] = {"type": type}
        if data is not None:
            body["data"] = data
        return await self._send(
            "POST",
            CALLBACK_ROUTE,
            json=body,
            files=files,
            query={"with_response": True},
            interaction_id=self.interaction.id,
            interaction_token=self.interaction.token,
        )

    async def _webhook(
        self,
        method: str,
        route: str,
        body: Mapping[str, Any] | None = None,
        files: Sequence[File] = (),
    ) -> bytes:
        return await self._send(
            method,
            route,
            json=body,
            files=files,
            application_id=self.interaction.application_id,
            interaction_token=self.interaction.token,
        )

    async def _send(self, method: str, route: str, **request: Any) -> bytes:
        try:
            response: bytes = await self.bot.http._request(method, route, **request)
        except HTTPException as error:
            if error.status == 401 or (
                error.status == 404
                and error.code
                in (JSONErrorCode.UNKNOWN_WEBHOOK, JSONErrorCode.UNKNOWN_INTERACTION)
            ):
                self.state = ResponseState.EXPIRED
            raise
        return response


class _ReplyingContext[
    I: CommandInteraction | ComponentInteraction,
    M: Message | WebhookMessage,
](_InteractionContext[I], ABC):
    _original_ephemeral = False
    _warned_ephemeral = False
    _auto_defer_task: "asyncio.Task[None] | None" = None

    def __init__(
        self,
        interaction: I,
        bot: "Bot",
        *,
        received: float,
        ephemeral: bool,
        auto_defer: float | None,
        allowed_mentions: AllowedMentions,
    ) -> None:
        super().__init__(interaction, bot, received=received)
        self._ephemeral = ephemeral
        self._auto_defer = auto_defer
        self._allowed_mentions = allowed_mentions

    @contextmanager
    def auto_defer(self) -> Iterator[None]:
        """Use as `with ctx.auto_defer():` to defer when the block is slow to answer.

        Slow means no answer `auto_defer` seconds after the interaction arrived.
        """
        timer = (
            None
            if self._auto_defer is None
            else self._loop.call_at(
                self.received + self._auto_defer, self._auto_defer_due
            )
        )
        try:
            yield
        finally:
            if timer is not None:
                timer.cancel()

    async def respond(
        self, content: str | None = None, **parts: Unpack[ReplyParts]
    ) -> WebhookMessage:
        """Answer, fill in the deferred answer, or send a follow-up, whichever fits."""
        body, files = self._message_body(content, REPLY_PARTS, parts)
        async with self._lock:
            self._ensure_alive()
            match self.state:
                case ResponseState.PENDING:
                    response = await self._respond_with_callback(body, files, parts)
                    self.state = ResponseState.RESPONDED
                    return self._answer(_from_callback(_webhook_message), response)
                case ResponseState.DEFERRED:
                    response = await self._edit_original(body, files, parts)
                case _:
                    response = await self._send_followup(body, files, parts)
            self.state = ResponseState.RESPONDED
            return self._answer(_webhook_message, response)

    async def defer(self, **options: Unpack[DeferOptions]) -> None:
        """Show "thinking…" and answer later with `respond`."""
        check_keys(options, DEFER_OPTIONS, "options")
        async with self._lock:
            self._ensure_alive()
            if self.state is ResponseState.RESPONDED:
                raise InteractionResponded("the interaction was already answered")
            if self.state is ResponseState.PENDING:
                await self._defer(ephemeral=options.get("ephemeral", self._ephemeral))

    async def edit(
        self, content: str | None = None, **parts: Unpack[ReplyEditParts]
    ) -> M:
        """Edit the original answer.

        With `files` or `attachments`, only the listed `attachments` stay.
        """
        body, files = self._edit_body(content, parts)
        async with self._lock:
            self._ensure_alive()
            self._ensure_answered("edit")
            response = await self._edit_original(body, files, parts)
            self._finish_deferred()
            return self._answer(self._decode_original, response)

    async def followup(
        self, content: str | None = None, **parts: Unpack[ReplyParts]
    ) -> WebhookMessage:
        """Send another message; right after `defer()` it fills the deferred answer."""
        body, files = self._message_body(content, REPLY_PARTS, parts)
        async with self._lock:
            self._ensure_alive()
            self._ensure_answered("send a follow-up to")
            if self.state is ResponseState.DEFERRED:
                response = await self._edit_original(body, files, parts)
            else:
                response = await self._send_followup(body, files, parts)
            self._finish_deferred()
            return self._answer(_webhook_message, response)

    async def delete(self) -> None:
        """Delete the original answer."""
        async with self._lock:
            self._ensure_alive()
            self._ensure_answered("delete the answer of")
            await self._webhook("DELETE", ORIGINAL_ROUTE)
            self._finish_deferred()

    def _ensure_answered(self, action: str) -> None:
        if self.state is ResponseState.PENDING:
            raise InteractionNotAnswered(
                f"cannot {action} an interaction that was not answered yet"
            )

    def _finish_deferred(self) -> None:
        if self.state is ResponseState.DEFERRED:
            self.state = ResponseState.RESPONDED

    def _auto_defer_due(self) -> None:
        if self.state is ResponseState.PENDING:
            self._auto_defer_task = self._loop.create_task(self._defer_unanswered())

    async def _defer_unanswered(self) -> None:
        try:
            async with self._lock:
                if self.state is ResponseState.PENDING:
                    await self._defer_automatically()
        except Exception:
            logger.warning("automatic defer of %s failed", self._label, exc_info=True)

    async def _defer_automatically(self) -> None:
        await self._defer(ephemeral=self._ephemeral)

    async def _defer(self, *, ephemeral: bool) -> None:
        await self._callback(
            InteractionCallbackType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE,
            {"flags": MessageFlags.EPHEMERAL} if ephemeral else None,
        )
        self.state = ResponseState.DEFERRED
        self._original_ephemeral = ephemeral

    async def _respond_with_callback(
        self, body: dict[str, Any], files: Sequence[File], parts: ReplyParts
    ) -> bytes:
        flagged = parts.get("ephemeral", self._ephemeral)
        response = await self._callback(
            InteractionCallbackType.CHANNEL_MESSAGE_WITH_SOURCE,
            _with_ephemeral(body, ephemeral=flagged),
            files,
        )
        self._original_ephemeral = flagged
        return response

    async def _send_followup(
        self, body: dict[str, Any], files: Sequence[File], parts: ReplyParts
    ) -> bytes:
        flagged = parts.get("ephemeral", self._ephemeral)
        return await self._webhook(
            "POST", FOLLOWUP_ROUTE, _with_ephemeral(body, ephemeral=flagged), files
        )

    async def _edit_original(
        self, body: dict[str, Any], files: Sequence[File], parts: ReplyParts
    ) -> bytes:
        self._warn_if_ephemeral_differs(parts)
        return await self._webhook("PATCH", ORIGINAL_ROUTE, body, files)

    def _warn_if_ephemeral_differs(self, parts: ReplyParts) -> None:
        ephemeral = parts.get("ephemeral", self._original_ephemeral)
        if ephemeral == self._original_ephemeral:
            return
        if not self._warned_ephemeral:
            self._warned_ephemeral = True
            logger.warning(
                "%s: ephemeral=%s is ignored because the message being edited is %s",
                self._label,
                ephemeral,
                "ephemeral" if self._original_ephemeral else "public",
            )

    def _message_body(
        self,
        content: str | None,
        allowed: frozenset[str],
        parts: ReplyParts,
        attachments: Sequence[Attachment | int] = (),
    ) -> tuple[dict[str, Any], Sequence[File]]:
        check_keys(parts, allowed, "message parts")
        parts.setdefault("allowed_mentions", self._allowed_mentions)
        return message_body(content, parts, attachments)

    def _edit_body(
        self, content: str | None, parts: ReplyEditParts
    ) -> tuple[dict[str, Any], Sequence[File]]:
        attachments = parts.get("attachments", ())
        return self._message_body(content, REPLY_EDIT_PARTS, parts, attachments)

    def _answer[A: PartialMessage](
        self, decode: Callable[[bytes], A], body: bytes
    ) -> A:
        return in_guild(decode_answer(decode, body, took_effect=True), self.guild_id)

    @abstractmethod
    def _decode_original(self, body: bytes) -> M: ...

    @property
    @abstractmethod
    def _label(self) -> str: ...


class Context(_ReplyingContext[CommandInteraction, WebhookMessage]):
    """The context of a slash command: who ran it, where, and how to answer."""

    @property
    def command_name(self) -> str:
        """The invoked command with its subcommands, such as `"role color set"`."""
        return _command_name(self.interaction.data)

    def _decode_original(self, body: bytes) -> WebhookMessage:
        return _webhook_message(body)

    @property
    def _label(self) -> str:
        return f"/{self.command_name}"


class ComponentContext(
    _ReplyingContext[ComponentInteraction, Message | WebhookMessage]
):
    """The context of a button or select menu use."""

    @property
    def custom_id(self) -> str:
        return self.interaction.data.custom_id

    @property
    def values(self) -> tuple[str, ...]:
        """The values picked in a select menu."""
        return self.interaction.data.values

    async def defer_update(self) -> None:
        """Acknowledge silently; edit the component's message later with `update`."""
        async with self._lock:
            self._ensure_alive()
            if self.state is ResponseState.RESPONDED:
                raise InteractionResponded("the interaction was already answered")
            if self.state is ResponseState.PENDING:
                await self._defer_update()

    async def update(
        self, content: str | None = None, **parts: Unpack[ReplyEditParts]
    ) -> Message | WebhookMessage:
        """Edit the message the component is attached to.

        With `files` or `attachments`, only the listed `attachments` stay.
        """
        body, files = self._edit_body(content, parts)
        async with self._lock:
            self._ensure_alive()
            match self.state:
                case ResponseState.PENDING:
                    response = await self._update_with_callback(body, files, parts)
                    self.state = ResponseState.RESPONDED
                    return self._answer(_from_callback(decode_message), response)
                case ResponseState.DEFERRED_UPDATE:
                    response = await self._edit_original(body, files, parts)
                case _:
                    raise InteractionResponded(
                        "update() needs an unanswered interaction or defer_update()"
                    )
            self.state = ResponseState.RESPONDED
            return self._answer(decode_message, response)

    async def _defer_automatically(self) -> None:
        await self._defer_update()

    async def _defer_update(self) -> None:
        await self._callback(InteractionCallbackType.DEFERRED_UPDATE_MESSAGE)
        self.state = ResponseState.DEFERRED_UPDATE
        self._original_ephemeral = self._component_message_is_ephemeral()

    async def _update_with_callback(
        self, body: dict[str, Any], files: Sequence[File], parts: ReplyParts
    ) -> bytes:
        self._original_ephemeral = self._component_message_is_ephemeral()
        self._warn_if_ephemeral_differs(parts)
        return await self._callback(InteractionCallbackType.UPDATE_MESSAGE, body, files)

    def _component_message_is_ephemeral(self) -> bool:
        message = decoder(_MessageFlagsOnly).decode(self.interaction._message)
        return MessageFlags.EPHEMERAL in message.flags

    def _decode_original(self, body: bytes) -> Message | WebhookMessage:
        return decode_message(body)

    @property
    def _label(self) -> str:
        return f"component {self.custom_id}"


class AutocompleteContext(_InteractionContext[AutocompleteInteraction]):
    """The context of an autocomplete request; it never defers."""

    @property
    def command_name(self) -> str:
        """The command being filled in, with its subcommands."""
        return _command_name(self.interaction.data)

    @property
    def options(self) -> dict[str, str | int | float | bool | None]:
        """The options filled so far, by name; required ones may be missing."""
        _, leaves = _split_subcommands(self.interaction.data.options)
        return {option.name: option.value for option in leaves}

    async def _respond(self, choices: list[dict[str, Any]]) -> None:
        async with self._lock:
            self._ensure_alive()
            if self.state is not ResponseState.PENDING:
                raise InteractionResponded("the interaction was already answered")
            await self._callback(
                InteractionCallbackType.APPLICATION_COMMAND_AUTOCOMPLETE_RESULT,
                {"choices": choices},
            )
            self.state = ResponseState.RESPONDED


def _split_subcommands(
    options: tuple[InteractionDataOption, ...],
) -> tuple[list[str], tuple[InteractionDataOption, ...]]:
    names: list[str] = []
    while options and options[0].type in SUBCOMMAND_TYPES:
        names.append(options[0].name)
        options = options[0].options
    return names, options


def _command_name(data: CommandData) -> str:
    names, _ = _split_subcommands(data.options)
    return " ".join((data.name, *names))


def _with_ephemeral(body: dict[str, Any], *, ephemeral: bool) -> dict[str, Any]:
    if not ephemeral:
        return body
    return {**body, "flags": body.get("flags", 0) | MessageFlags.EPHEMERAL}


def _webhook_message(body: bytes) -> WebhookMessage:
    return decode(WebhookMessage, body)


def _from_callback[A](decode: Callable[[bytes], A]) -> Callable[[bytes], A]:
    def from_resource(body: bytes) -> A:
        resource = decoder(_CallbackResponse).decode(body).resource
        return decode(bytes(resource.message))

    return from_resource
