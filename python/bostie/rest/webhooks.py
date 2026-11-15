from collections.abc import Mapping, Sequence
from typing import Any, Required, TypedDict, Unpack

from bostie.file import File
from bostie.models import (
    EditParts,
    MessageParts,
    PollCreateRequest,
    Webhook,
    WebhookMessage,
)
from bostie.models.message import _message_body
from bostie.rest._base import ResourceGroup, each, wire


class WebhookCreate(TypedDict, total=False):
    name: Required[str]
    avatar: File | None


class WebhookEdit(TypedDict, total=False):
    name: str
    avatar: File | None
    channel_id: int


class WebhookEditWithToken(TypedDict, total=False):
    name: str
    avatar: File | None


class WebhookExecute(MessageParts, total=False):
    """The message to send, how the webhook shows, and where it posts."""

    username: str
    avatar_url: str
    thread_name: str
    applied_tags: Sequence[int]
    thread_id: int
    with_components: bool


class WebhookEditMessage(EditParts, total=False):
    """The parts to edit; `poll` is only accepted on a deferred interaction answer."""

    poll: PollCreateRequest | None
    thread_id: int
    with_components: bool


class WebhookMessageIn(TypedDict, total=False):
    thread_id: int


class WebhookExecuteCompatible(TypedDict, total=False):
    thread_id: int
    wait: bool


class Webhooks(ResourceGroup):
    async def create(
        self,
        channel_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[WebhookCreate],
    ) -> Webhook:
        return await self._call(
            Webhook,
            "POST",
            "/channels/{channel_id}/webhooks",
            json=wire(options, WebhookCreate),
            reason=reason,
            channel_id=channel_id,
        )

    async def delete(self, webhook_id: int, /, *, reason: str | None = None) -> None:
        await self._http.send(
            "DELETE", "/webhooks/{webhook_id}", reason=reason, webhook_id=webhook_id
        )

    async def delete_message(
        self,
        webhook_id: int,
        webhook_token: str,
        message_id: int,
        /,
        **options: Unpack[WebhookMessageIn],
    ) -> None:
        await self._http.send(
            "DELETE",
            "/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}",
            query=wire(options, WebhookMessageIn),
            webhook_id=webhook_id,
            webhook_token=webhook_token,
            message_id=message_id,
        )

    async def delete_with_token(self, webhook_id: int, webhook_token: str, /) -> None:
        await self._http.send(
            "DELETE",
            "/webhooks/{webhook_id}/{webhook_token}",
            webhook_id=webhook_id,
            webhook_token=webhook_token,
        )

    async def edit(
        self,
        webhook_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[WebhookEdit],
    ) -> Webhook:
        return await self._call(
            Webhook,
            "PATCH",
            "/webhooks/{webhook_id}",
            json=wire(options, WebhookEdit),
            reason=reason,
            webhook_id=webhook_id,
        )

    async def edit_message(
        self,
        webhook_id: int,
        webhook_token: str,
        message_id: int,
        /,
        **parts: Unpack[WebhookEditMessage],
    ) -> WebhookMessage:
        """Edit a message the webhook sent: a part not given stays and None clears it.

        With `files` or `attachments`, only the listed `attachments` stay.
        """
        given = wire(parts, WebhookEditMessage)
        query = {
            "thread_id": given.pop("thread_id", None),
            "with_components": given.pop("with_components", None),
        }
        body, files = _message_body(None, given, mentions=self._allowed_mentions)
        message = await self._call(
            WebhookMessage,
            "PATCH",
            "/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}",
            json=body,
            files=files,
            query=query,
            webhook_id=webhook_id,
            webhook_token=webhook_token,
            message_id=message_id,
        )
        return self._in_guild(message)

    async def edit_with_token(
        self,
        webhook_id: int,
        webhook_token: str,
        /,
        **options: Unpack[WebhookEditWithToken],
    ) -> Webhook:
        return await self._call(
            Webhook,
            "PATCH",
            "/webhooks/{webhook_id}/{webhook_token}",
            json=wire(options, WebhookEditWithToken),
            webhook_id=webhook_id,
            webhook_token=webhook_token,
        )

    async def execute(
        self,
        webhook_id: int,
        webhook_token: str,
        /,
        content: str | None = None,
        **parts: Unpack[WebhookExecute],
    ) -> WebhookMessage:
        """Send a message through the webhook and return it once Discord saved it."""
        given = wire(parts, WebhookExecute)
        query = {
            "wait": True,
            "thread_id": given.pop("thread_id", None),
            "with_components": given.pop("with_components", None),
        }
        body, files = _message_body(content, given, mentions=self._allowed_mentions)
        message = await self._call(
            WebhookMessage,
            "POST",
            "/webhooks/{webhook_id}/{webhook_token}",
            json=body,
            files=files,
            query=query,
            webhook_id=webhook_id,
            webhook_token=webhook_token,
        )
        return self._in_guild(message)

    async def execute_github(
        self,
        webhook_id: int,
        webhook_token: str,
        /,
        payload: Mapping[str, Any],
        **options: Unpack[WebhookExecuteCompatible],
    ) -> None:
        """Post a GitHub webhook event payload, as GitHub would."""
        await self._http.send(
            "POST",
            "/webhooks/{webhook_id}/{webhook_token}/github",
            json=payload,
            query=wire(options, WebhookExecuteCompatible),
            webhook_id=webhook_id,
            webhook_token=webhook_token,
        )

    async def execute_slack(
        self,
        webhook_id: int,
        webhook_token: str,
        /,
        payload: Mapping[str, Any],
        **options: Unpack[WebhookExecuteCompatible],
    ) -> None:
        """Post a message in Slack's incoming webhook format."""
        await self._http.send(
            "POST",
            "/webhooks/{webhook_id}/{webhook_token}/slack",
            json=payload,
            query=wire(options, WebhookExecuteCompatible),
            webhook_id=webhook_id,
            webhook_token=webhook_token,
        )

    async def fetch(self, webhook_id: int, /) -> Webhook:
        return await self._call(
            Webhook,
            "GET",
            "/webhooks/{webhook_id}",
            webhook_id=webhook_id,
        )

    async def fetch_message(
        self,
        webhook_id: int,
        webhook_token: str,
        message_id: int,
        /,
        **options: Unpack[WebhookMessageIn],
    ) -> WebhookMessage:
        message = await self._call(
            WebhookMessage,
            "GET",
            "/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}",
            query=wire(options, WebhookMessageIn),
            webhook_id=webhook_id,
            webhook_token=webhook_token,
            message_id=message_id,
        )
        return self._in_guild(message)

    async def fetch_with_token(self, webhook_id: int, webhook_token: str, /) -> Webhook:
        return await self._call(
            Webhook,
            "GET",
            "/webhooks/{webhook_id}/{webhook_token}",
            webhook_id=webhook_id,
            webhook_token=webhook_token,
        )

    async def list(self, channel_id: int, /) -> tuple[Webhook, ...]:
        return await self._call(
            each(Webhook),
            "GET",
            "/channels/{channel_id}/webhooks",
            channel_id=channel_id,
        )

    async def list_guild(self, guild_id: int, /) -> tuple[Webhook, ...]:
        return await self._call(
            each(Webhook),
            "GET",
            "/guilds/{guild_id}/webhooks",
            guild_id=guild_id,
        )
