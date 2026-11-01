import asyncio
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from enum import Enum
from typing import TYPE_CHECKING, Any

import msgspec

from bostie.enums import MessageFlags, OptionType
from bostie.errors import HTTPException, InteractionExpired, InteractionResponded
from bostie.file import File
from bostie.models import (
    AllowedMentions,
    Attachment,
    Channel,
    Component,
    Embed,
    Interaction,
    InteractionDataOption,
    Member,
    Message,
    User,
    build_message,
    decode,
)
from bostie.models.base import decoder
from bostie.models.message import in_guild
from bostie.voice.connection import ContextVoice

if TYPE_CHECKING:
    from bostie.bot import Bot
    from bostie.http import HTTPClient

logger = logging.getLogger("bostie.context")

TOKEN_LIFETIME = 15 * 60
CALLBACK_ROUTE = "/interactions/{interaction_id}/{interaction_token}/callback"
ORIGINAL_ROUTE = "/webhooks/{application_id}/{interaction_token}/messages/@original"
FOLLOWUP_ROUTE = "/webhooks/{application_id}/{interaction_token}"

CHANNEL_MESSAGE = 4
DEFERRED_CHANNEL_MESSAGE = 5
DEFERRED_UPDATE_MESSAGE = 6
UPDATE_MESSAGE = 7
AUTOCOMPLETE_RESULT = 8
UNKNOWN_WEBHOOK = 10015
UNKNOWN_INTERACTION = 10062

SUBCOMMAND_TYPES = (OptionType.SUB_COMMAND, OptionType.SUB_COMMAND_GROUP)


class ResponseState(Enum):
    PENDING = "pending"
    DEFERRED = "deferred"
    DEFERRED_UPDATE = "deferred_update"
    RESPONDED = "responded"
    EXPIRED = "expired"


class _CallbackResource(msgspec.Struct):
    message: msgspec.Raw = msgspec.Raw()


class _CallbackResponse(msgspec.Struct):
    resource: _CallbackResource | None = None


class _InteractionContext:
    def __init__(
        self,
        interaction: Interaction,
        bot: Any,
        http: Any,
        *,
        received: float,
        ephemeral: bool,
        auto_defer: float | None,
        allowed_mentions: AllowedMentions | None,
    ) -> None:
        self.interaction = interaction
        self.bot: Bot = bot
        self.received = received
        self.state = ResponseState.PENDING
        self._http: HTTPClient = http
        self._ephemeral = ephemeral
        self._auto_defer = auto_defer
        self._allowed_mentions = allowed_mentions
        self._lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()

    @property
    def user(self) -> User:
        """The user who triggered the interaction, in a guild or in a DM."""
        author = self.interaction.author
        if author is None:
            raise RuntimeError(
                f"Discord sent interaction {self.interaction.id} without a user"
            )
        return author

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
    def channel(self) -> Channel | None:
        return self.interaction.channel

    @property
    def locale(self) -> str | None:
        return self.interaction.locale

    @property
    def voice(self) -> ContextVoice:
        """Voice in this guild; `connect()` joins the channel of the user who ran it."""
        return ContextVoice(self.bot.voice, self.guild_id, self.user.id)

    @property
    def command_name(self) -> str | None:
        """The invoked command with its subcommands, such as `"role color set"`."""
        data = self.interaction.data
        if data is None or data.name is None:
            return None
        names, _ = _split_subcommands(data.options)
        return " ".join((data.name, *names))

    def _ensure_alive(self) -> None:
        if self.state is ResponseState.EXPIRED:
            raise InteractionExpired("Discord no longer accepts this interaction")
        if self._loop.time() - self.received > TOKEN_LIFETIME:
            raise InteractionExpired(
                "interactions can only be answered within 15 minutes"
            )

    async def _callback(
        self,
        type: int,
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
            response: bytes = await self._http._request(method, route, **request)
        except HTTPException as error:
            if error.status == 401 or (
                error.status == 404
                and error.code in (UNKNOWN_WEBHOOK, UNKNOWN_INTERACTION)
            ):
                self.state = ResponseState.EXPIRED
            raise
        return response


class Context(_InteractionContext):
    """The context of a slash command: who ran it, where, and how to answer."""

    _original_ephemeral = False
    _warned_ephemeral = False
    _auto_defer_task: "asyncio.Task[None] | None" = None

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
        self,
        content: str | None = None,
        *,
        embeds: Sequence[Embed | Mapping[str, Any]] | None = None,
        components: Sequence[Component | Mapping[str, Any]] | None = None,
        files: Sequence[File] = (),
        ephemeral: bool | None = None,
        allowed_mentions: AllowedMentions | None = None,
        tts: bool | None = None,
        flags: int | None = None,
    ) -> Message | None:
        """Answer, fill in the deferred answer, or send a follow-up, whichever fits."""
        body = self._message_body(
            content,
            allowed_mentions,
            embeds=embeds,
            components=components,
            files=files,
            tts=tts,
            flags=flags,
        )
        async with self._lock:
            self._ensure_alive()
            match self.state:
                case ResponseState.PENDING:
                    response = await self._respond_with_callback(body, files, ephemeral)
                    self.state = ResponseState.RESPONDED
                    return _callback_message(response, self.guild_id)
                case ResponseState.DEFERRED:
                    response = await self._edit_original(body, files, ephemeral)
                case _:
                    response = await self._send_followup(body, files, ephemeral)
            self.state = ResponseState.RESPONDED
            return _message(response, self.guild_id)

    async def defer(self, *, ephemeral: bool | None = None) -> None:
        """Show "thinking…" and answer later with `respond`."""
        async with self._lock:
            self._ensure_alive()
            if self.state is ResponseState.RESPONDED:
                raise InteractionResponded("the interaction was already answered")
            if self.state is ResponseState.PENDING:
                await self._defer(self._ephemeral if ephemeral is None else ephemeral)

    async def edit(
        self,
        content: str | None = None,
        *,
        embeds: Sequence[Embed | Mapping[str, Any]] | None = None,
        components: Sequence[Component | Mapping[str, Any]] | None = None,
        files: Sequence[File] = (),
        attachments: Sequence[Attachment | int] = (),
        ephemeral: bool | None = None,
        allowed_mentions: AllowedMentions | None = None,
        tts: bool | None = None,
        flags: int | None = None,
    ) -> Message | None:
        """Edit the original answer.

        With `files` or `attachments`, only the listed `attachments` stay.
        """
        body = self._message_body(
            content,
            allowed_mentions,
            embeds=embeds,
            components=components,
            files=files,
            attachments=attachments,
            tts=tts,
            flags=flags,
        )
        async with self._lock:
            self._ensure_alive()
            self._ensure_answered("edit")
            response = await self._edit_original(body, files, ephemeral)
            self._finish_deferred()
            return _message(response, self.guild_id)

    async def followup(
        self,
        content: str | None = None,
        *,
        embeds: Sequence[Embed | Mapping[str, Any]] | None = None,
        components: Sequence[Component | Mapping[str, Any]] | None = None,
        files: Sequence[File] = (),
        ephemeral: bool | None = None,
        allowed_mentions: AllowedMentions | None = None,
        tts: bool | None = None,
        flags: int | None = None,
    ) -> Message | None:
        """Send another message; right after `defer()` it fills the deferred answer."""
        body = self._message_body(
            content,
            allowed_mentions,
            embeds=embeds,
            components=components,
            files=files,
            tts=tts,
            flags=flags,
        )
        async with self._lock:
            self._ensure_alive()
            self._ensure_answered("send a follow-up to")
            if self.state is ResponseState.DEFERRED:
                response = await self._edit_original(body, files, ephemeral)
            else:
                response = await self._send_followup(body, files, ephemeral)
            self._finish_deferred()
            return _message(response, self.guild_id)

    async def delete(self) -> None:
        """Delete the original answer."""
        async with self._lock:
            self._ensure_alive()
            self._ensure_answered("delete the answer of")
            await self._webhook("DELETE", ORIGINAL_ROUTE)
            self._finish_deferred()

    def _ensure_answered(self, action: str) -> None:
        if self.state is ResponseState.PENDING:
            raise InteractionResponded(
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
            logger.warning(
                "automatic defer of /%s failed", self.command_name, exc_info=True
            )

    async def _defer_automatically(self) -> None:
        await self._defer(self._ephemeral)

    async def _defer(self, ephemeral: bool) -> None:
        await self._callback(
            DEFERRED_CHANNEL_MESSAGE,
            {"flags": MessageFlags.EPHEMERAL} if ephemeral else None,
        )
        self.state = ResponseState.DEFERRED
        self._original_ephemeral = ephemeral

    async def _respond_with_callback(
        self, body: dict[str, Any], files: Sequence[File], ephemeral: bool | None
    ) -> bytes:
        flagged = self._ephemeral_for_new_message(ephemeral)
        response = await self._callback(
            CHANNEL_MESSAGE, _with_ephemeral(body, flagged), files
        )
        self._original_ephemeral = flagged
        return response

    async def _send_followup(
        self, body: dict[str, Any], files: Sequence[File], ephemeral: bool | None
    ) -> bytes:
        flagged = self._ephemeral_for_new_message(ephemeral)
        return await self._webhook(
            "POST", FOLLOWUP_ROUTE, _with_ephemeral(body, flagged), files
        )

    async def _edit_original(
        self, body: dict[str, Any], files: Sequence[File], ephemeral: bool | None
    ) -> bytes:
        self._warn_if_ephemeral_differs(ephemeral)
        return await self._webhook("PATCH", ORIGINAL_ROUTE, body, files)

    def _ephemeral_for_new_message(self, ephemeral: bool | None) -> bool:
        return self._ephemeral if ephemeral is None else ephemeral

    def _warn_if_ephemeral_differs(self, ephemeral: bool | None) -> None:
        if ephemeral is None or ephemeral == self._original_ephemeral:
            return
        if not self._warned_ephemeral:
            self._warned_ephemeral = True
            logger.warning(
                "/%s: ephemeral=%s is ignored because the message being edited is %s",
                self.command_name,
                ephemeral,
                "ephemeral" if self._original_ephemeral else "public",
            )

    def _message_body(
        self,
        content: str | None,
        allowed_mentions: AllowedMentions | None,
        **parts: Any,
    ) -> dict[str, Any]:
        body, _ = build_message(
            content,
            allowed_mentions=self._allowed_mentions
            if allowed_mentions is None
            else allowed_mentions,
            **parts,
        )
        return body


class ComponentContext(Context):
    """The context of a button or select menu use."""

    @property
    def custom_id(self) -> str | None:
        data = self.interaction.data
        return None if data is None else data.custom_id

    @property
    def values(self) -> tuple[str, ...]:
        """The values picked in a select menu."""
        data = self.interaction.data
        return () if data is None else data.values

    async def defer_update(self) -> None:
        """Acknowledge silently; edit the component's message later with `update`."""
        async with self._lock:
            self._ensure_alive()
            if self.state is ResponseState.RESPONDED:
                raise InteractionResponded("the interaction was already answered")
            if self.state is ResponseState.PENDING:
                await self._defer_update()

    async def update(
        self,
        content: str | None = None,
        *,
        embeds: Sequence[Embed | Mapping[str, Any]] | None = None,
        components: Sequence[Component | Mapping[str, Any]] | None = None,
        files: Sequence[File] = (),
        attachments: Sequence[Attachment | int] = (),
        ephemeral: bool | None = None,
        allowed_mentions: AllowedMentions | None = None,
        tts: bool | None = None,
        flags: int | None = None,
    ) -> Message | None:
        """Edit the message the component is attached to.

        With `files` or `attachments`, only the listed `attachments` stay.
        """
        body = self._message_body(
            content,
            allowed_mentions,
            embeds=embeds,
            components=components,
            files=files,
            attachments=attachments,
            tts=tts,
            flags=flags,
        )
        async with self._lock:
            self._ensure_alive()
            match self.state:
                case ResponseState.PENDING:
                    response = await self._update_with_callback(body, files, ephemeral)
                    self.state = ResponseState.RESPONDED
                    return _callback_message(response, self.guild_id)
                case ResponseState.DEFERRED_UPDATE:
                    response = await self._edit_original(body, files, ephemeral)
                case _:
                    raise InteractionResponded(
                        "update() needs an unanswered interaction or defer_update()"
                    )
            self.state = ResponseState.RESPONDED
            return _message(response, self.guild_id)

    async def _defer_automatically(self) -> None:
        await self._defer_update()

    async def _defer_update(self) -> None:
        await self._callback(DEFERRED_UPDATE_MESSAGE)
        self.state = ResponseState.DEFERRED_UPDATE
        self._original_ephemeral = self._component_message_is_ephemeral()

    async def _update_with_callback(
        self, body: dict[str, Any], files: Sequence[File], ephemeral: bool | None
    ) -> bytes:
        self._original_ephemeral = self._component_message_is_ephemeral()
        self._warn_if_ephemeral_differs(ephemeral)
        return await self._callback(UPDATE_MESSAGE, body, files)

    def _component_message_is_ephemeral(self) -> bool:
        message = self.interaction.message
        flags = None if message is None else message.flags
        return bool((flags or 0) & MessageFlags.EPHEMERAL)


class AutocompleteContext(_InteractionContext):
    """The context of an autocomplete request; it never defers."""

    @property
    def options(self) -> dict[str, str | int | float | bool | None]:
        """The options filled so far, by name; required ones may be missing."""
        data = self.interaction.data
        _, leaves = _split_subcommands(() if data is None else data.options)
        return {option.name: option.value for option in leaves if option.name}

    async def _respond(self, choices: list[dict[str, Any]]) -> None:
        async with self._lock:
            self._ensure_alive()
            if self.state is not ResponseState.PENDING:
                raise InteractionResponded("the interaction was already answered")
            await self._callback(AUTOCOMPLETE_RESULT, {"choices": choices})
            self.state = ResponseState.RESPONDED


def _split_subcommands(
    options: tuple[InteractionDataOption, ...],
) -> tuple[list[str], tuple[InteractionDataOption, ...]]:
    names: list[str] = []
    while options and options[0].type in SUBCOMMAND_TYPES:
        names.append(options[0].name or "")
        options = options[0].options
    return names, options


def _with_ephemeral(body: dict[str, Any], ephemeral: bool) -> dict[str, Any]:
    if not ephemeral:
        return body
    return {**body, "flags": body.get("flags", 0) | MessageFlags.EPHEMERAL}


def _message(body: bytes, guild_id: int | None) -> Message | None:
    return in_guild(decode(Message, body), guild_id) if body else None


def _callback_message(body: bytes, guild_id: int | None) -> Message | None:
    resource = decoder(_CallbackResponse).decode(body).resource if body else None
    if resource is None or not resource.message:
        return None
    return in_guild(decode(Message, bytes(resource.message)), guild_id)
