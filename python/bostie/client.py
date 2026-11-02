import logging
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self, override

from bostie._bridge import Bridge, GatewayDelivery
from bostie.bot import DEFAULT_ALLOWED_MENTIONS, _resolve_token
from bostie.cache import Cache
from bostie.errors import StateError
from bostie.http import HTTPClient
from bostie.models import AllowedMentions
from bostie.rest import REST

log = logging.getLogger("bostie.rest")

API_URL = "https://discord.com/api/v10"


class _Client[G](ABC):
    def __init__(
        self,
        token: str | None,
        *,
        bearer: bool,
        api_url: str,
        global_rate_limit: int,
    ) -> None:
        self._token = token
        self._bearer = bearer
        self._api_url = api_url
        self._global_rate_limit = global_rate_limit
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
            global_limit=self._global_rate_limit,
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
        api_url: str = API_URL,
        global_rate_limit: int = 50,
        allowed_mentions: AllowedMentions = DEFAULT_ALLOWED_MENTIONS,
    ) -> None:
        super().__init__(
            _resolve_token(token),
            bearer=False,
            api_url=api_url,
            global_rate_limit=global_rate_limit,
        )
        self._allowed_mentions = allowed_mentions

    @property
    def rest(self) -> REST:
        """Discord's REST operations by resource, as on `bot.rest`."""
        return self._opened

    @override
    def _open(self, http: HTTPClient) -> REST:
        return REST(http, self._allowed_mentions, Cache())


def _on_delivery(delivery: GatewayDelivery, _received: float) -> None:
    match delivery:
        case ("invalid_requests", count):
            log.warning(
                "%d invalid requests (401, 403, 429) in the last 10 minutes; "
                "Discord bans the IP at 10 000",
                count,
            )
        case _:
            pass
