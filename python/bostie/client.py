import asyncio
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from types import TracebackType
from typing import Any, Self, Unpack, override
from urllib.parse import urlsplit

from bostie._bridge import Bridge, GatewayDelivery, warn_invalid_requests
from bostie._util import resolve_token
from bostie.errors import StateError
from bostie.http import DEFAULT_HTTP, HTTPClient, HTTPSettings
from bostie.models import AllowedMentions, Webhook, WebhookMessage
from bostie.models.message import DEFAULT_ALLOWED_MENTIONS
from bostie.rest import REST, BearerREST
from bostie.rest.webhooks import (
    WebhookEditMessage,
    WebhookEditWithToken,
    WebhookExecute,
    WebhookExecuteCompatible,
    WebhookMessageIn,
    Webhooks,
)

_WEBHOOK_PATH = re.compile(r"/webhooks/(?P<id>\d+)/(?P<token>[^/]+)/?")


class _Client[G](ABC):
    def __init__(
        self,
        token: str | None,
        *,
        bearer: bool,
        api_url: str | None,
        http_settings: HTTPSettings,
    ) -> None:
        self._token = token
        self._bearer = bearer
        self._api_url = api_url
        self._http_settings = http_settings
        self._bridge: Bridge | None = None
        self._http: HTTPClient | None = None
        self._groups: G | None = None

    @property
    def http(self) -> HTTPClient:
        """The client for any endpoint, inside `async with`."""
        if self._http is None:
            raise StateError(f"a {type(self).__name__} works inside 'async with'")
        return self._http

    async def __aenter__(self) -> Self:
        if self._bridge is not None:
            raise StateError(f"the {type(self).__name__} is already open")
        bridge = Bridge(
            self._token,
            self._api_url,
            _on_delivery,
            http=self._http_settings,
            bearer=self._bearer,
        )
        await bridge.__aenter__()
        self._bridge = bridge
        self._http = HTTPClient(bridge)
        self._groups = self._open(self._http)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        bridge = self._bridge
        self._bridge = self._http = self._groups = None
        if bridge is not None:
            await bridge.close()

    @property
    def _opened(self) -> G:
        if self._groups is None:
            raise StateError(f"a {type(self).__name__} works inside 'async with'")
        return self._groups

    @abstractmethod
    def _open(self, http: HTTPClient) -> G: ...


class RESTClient(_Client[REST]):
    """`bot.rest` without a gateway, for scripts and web backends.

    Open it with `async with RESTClient(token) as client:`; the token defaults to
    the BOSTIE_TOKEN environment variable.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        api_url: str | None = None,
        http_settings: HTTPSettings = DEFAULT_HTTP,
        allowed_mentions: AllowedMentions = DEFAULT_ALLOWED_MENTIONS,
    ) -> None:
        super().__init__(
            resolve_token(token),
            bearer=False,
            api_url=api_url,
            http_settings=http_settings,
        )
        self._allowed_mentions = allowed_mentions
        self._deletions: set[asyncio.Task[None]] = set()

    @property
    def rest(self) -> REST:
        """Discord's REST operations by resource, as on `bot.rest`."""
        return self._opened

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for deletion in self._deletions:
            deletion.cancel()
        await super().__aexit__(exc_type, exc, traceback)

    @override
    def _open(self, http: HTTPClient) -> REST:
        return REST(http, self._allowed_mentions, None, self._deletions)


class BearerClient(_Client[BearerREST]):
    """What an OAuth2 access token reaches, for the user who granted it.

    Open it with `async with BearerClient(access_token) as user:`. Without a token
    it only reaches `rest.oauth2`, to exchange a code or credentials for one.
    """

    def __init__(
        self,
        access_token: str | None = None,
        *,
        api_url: str | None = None,
        http_settings: HTTPSettings = DEFAULT_HTTP,
    ) -> None:
        super().__init__(
            access_token,
            bearer=True,
            api_url=api_url,
            http_settings=http_settings,
        )

    @property
    def rest(self) -> BearerREST:
        return self._opened

    @override
    def _open(self, http: HTTPClient) -> BearerREST:
        return BearerREST(http, DEFAULT_ALLOWED_MENTIONS, None)


class WebhookClient(_Client[Webhooks]):
    """One webhook, from its URL alone, with no bot token.

    Open it with `async with WebhookClient(url) as webhook:`.
    """

    def __init__(
        self,
        url: str,
        *,
        api_url: str | None = None,
        http_settings: HTTPSettings = DEFAULT_HTTP,
        allowed_mentions: AllowedMentions = DEFAULT_ALLOWED_MENTIONS,
    ) -> None:
        found = _WEBHOOK_PATH.search(urlsplit(url).path)
        if found is None:
            raise ValueError(f"{url!r} is not a webhook URL")
        super().__init__(
            None, bearer=False, api_url=api_url, http_settings=http_settings
        )
        self.id = int(found["id"])
        self.token: str = found["token"]
        self._allowed_mentions = allowed_mentions

    async def delete(self) -> None:
        await self._opened.delete_with_token(self.id, self.token)

    async def delete_message(
        self, message_id: int, /, **options: Unpack[WebhookMessageIn]
    ) -> None:
        await self._opened.delete_message(self.id, self.token, message_id, **options)

    async def edit(self, **options: Unpack[WebhookEditWithToken]) -> Webhook:
        return await self._opened.edit_with_token(self.id, self.token, **options)

    async def edit_message(
        self, message_id: int, /, **parts: Unpack[WebhookEditMessage]
    ) -> WebhookMessage:
        """Edit what the webhook sent: a part not given stays and None clears it."""
        return await self._opened.edit_message(self.id, self.token, message_id, **parts)

    async def execute(
        self, content: str | None = None, **parts: Unpack[WebhookExecute]
    ) -> WebhookMessage:
        """Send a message and return it once Discord saved it."""
        return await self._opened.execute(self.id, self.token, content, **parts)

    async def execute_github(
        self, payload: Mapping[str, Any], **options: Unpack[WebhookExecuteCompatible]
    ) -> None:
        """Post a GitHub webhook event payload, as GitHub would."""
        await self._opened.execute_github(self.id, self.token, payload, **options)

    async def execute_slack(
        self, payload: Mapping[str, Any], **options: Unpack[WebhookExecuteCompatible]
    ) -> None:
        """Post a message in Slack's incoming webhook format."""
        await self._opened.execute_slack(self.id, self.token, payload, **options)

    async def fetch(self) -> Webhook:
        return await self._opened.fetch_with_token(self.id, self.token)

    async def fetch_message(
        self, message_id: int, /, **options: Unpack[WebhookMessageIn]
    ) -> WebhookMessage:
        return await self._opened.fetch_message(
            self.id, self.token, message_id, **options
        )

    @override
    def _open(self, http: HTTPClient) -> Webhooks:
        return Webhooks(http, self._allowed_mentions, None)


class SyncWebhookClient:
    """A WebhookClient for code without an event loop, each call running its own."""

    def __init__(
        self,
        url: str,
        *,
        api_url: str | None = None,
        http_settings: HTTPSettings = DEFAULT_HTTP,
        allowed_mentions: AllowedMentions = DEFAULT_ALLOWED_MENTIONS,
    ) -> None:
        self._client = partial(
            WebhookClient,
            url,
            api_url=api_url,
            http_settings=http_settings,
            allowed_mentions=allowed_mentions,
        )
        checked = self._client()
        self.id = checked.id
        self.token = checked.token

    def delete(self) -> None:
        self._run(WebhookClient.delete)

    def delete_message(
        self, message_id: int, /, **options: Unpack[WebhookMessageIn]
    ) -> None:
        self._run(lambda webhook: webhook.delete_message(message_id, **options))

    def edit(self, **options: Unpack[WebhookEditWithToken]) -> Webhook:
        return self._run(lambda webhook: webhook.edit(**options))

    def edit_message(
        self, message_id: int, /, **parts: Unpack[WebhookEditMessage]
    ) -> WebhookMessage:
        """Edit what the webhook sent: a part not given stays and None clears it."""
        return self._run(lambda webhook: webhook.edit_message(message_id, **parts))

    def execute(
        self, content: str | None = None, **parts: Unpack[WebhookExecute]
    ) -> WebhookMessage:
        """Send a message and return it once Discord saved it."""
        return self._run(lambda webhook: webhook.execute(content, **parts))

    def execute_github(
        self, payload: Mapping[str, Any], **options: Unpack[WebhookExecuteCompatible]
    ) -> None:
        """Post a GitHub webhook event payload, as GitHub would."""
        self._run(lambda webhook: webhook.execute_github(payload, **options))

    def execute_slack(
        self, payload: Mapping[str, Any], **options: Unpack[WebhookExecuteCompatible]
    ) -> None:
        """Post a message in Slack's incoming webhook format."""
        self._run(lambda webhook: webhook.execute_slack(payload, **options))

    def fetch(self) -> Webhook:
        return self._run(WebhookClient.fetch)

    def fetch_message(
        self, message_id: int, /, **options: Unpack[WebhookMessageIn]
    ) -> WebhookMessage:
        return self._run(lambda webhook: webhook.fetch_message(message_id, **options))

    def _run[T](self, call: Callable[[WebhookClient], Awaitable[T]]) -> T:
        async def run() -> T:
            async with self._client() as webhook:
                return await call(webhook)

        return asyncio.run(run())


def _on_delivery(delivery: GatewayDelivery, _received: float) -> None:
    match delivery:
        case ("invalid_requests", count):
            warn_invalid_requests(count)
        case _:
            pass
