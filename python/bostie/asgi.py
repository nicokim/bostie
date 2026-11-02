import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import msgspec

from bostie._bridge import Bridge
from bostie.bot import Bot, _resolve_token
from bostie.client import _on_delivery
from bostie.enums import InteractionType
from bostie.errors import BostieError
from bostie.events import InteractionCreate
from bostie.http import HTTPClient
from bostie.models.base import decoder
from bostie.rest import REST
from bostie.webhook_events import _webhook_event, verify_signature

MAX_BODY = 4 * 1024 * 1024
PONG = b'{"type":1}'

type Message = dict[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]


class _Kind(msgspec.Struct):
    type: int = 0
    token: str | None = None


class ASGIApp:
    """An ASGI app for the app's Interactions Endpoint and Webhook Events URLs.

    Interactions reach the bot's commands and routes, webhook events its listeners.
    `start()`, which the server's lifespan calls, starts the bot's REST without a
    gateway unless the bot already runs, and reads the app's public key.
    """

    def __init__(
        self, bot: Bot, *, token: str | None = None, public_key: str | None = None
    ) -> None:
        self.bot = bot
        self._token = token
        self._public_key = public_key
        self._owns_bot = False

    async def __call__(
        self, scope: Mapping[str, Any], receive: Receive, send: Send
    ) -> None:
        match scope["type"]:
            case "lifespan":
                await self._lifespan(receive, send)
            case "http":
                await self._request(scope, receive, send)
            case _:
                pass

    async def start(self) -> None:
        """Start the bot's REST if the bot is not running, sync its commands."""
        bot = self.bot
        if bot._bridge is None:
            bridge = Bridge(
                _resolve_token(self._token),
                bot._api_url,
                _on_delivery,
                http=bot._http_settings,
            )
            await bridge.__aenter__()
            bot._bridge = bridge
            bot._http = HTTPClient(bridge)
            bot._rest = REST(bot._http, bot._allowed_mentions, bot.cache)
            self._owns_bot = True
            try:
                await self._prepare(bot)
            except BaseException:
                await self.close()
                raise
            bot._start_tasks()
        elif self._public_key is None:
            self._public_key = (await bot.rest.application.fetch()).verify_key

    async def close(self) -> None:
        """Close the bot if `start()` started it."""
        if self._owns_bot:
            self._owns_bot = False
            await self.bot.close()

    async def _prepare(self, bot: Bot) -> None:
        application = await bot.rest.application.fetch()
        bot.application_id = application.id
        if self._public_key is None:
            self._public_key = application.verify_key
        bot.user = bot.cache._current = await bot.rest.users.fetch_current()
        await bot._setup()
        if bot._auto_sync:
            await bot._sync(always_put=False)

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            match message["type"]:
                case "lifespan.startup":
                    try:
                        await self.start()
                    except (BostieError, ValueError) as error:
                        await send(
                            {"type": "lifespan.startup.failed", "message": str(error)}
                        )
                        return
                    await send({"type": "lifespan.startup.complete"})
                case "lifespan.shutdown":
                    await self.close()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
                case _:
                    pass

    async def _request(
        self, scope: Mapping[str, Any], receive: Receive, send: Send
    ) -> None:
        if scope["method"] != "POST":
            await _respond(send, 405)
            return
        body = await _body(receive)
        if body is None:
            await _respond(send, 413)
            return
        headers: dict[bytes, bytes] = dict(scope["headers"])
        signature = headers.get(b"x-signature-ed25519", b"").decode("latin-1")
        timestamp = headers.get(b"x-signature-timestamp", b"").decode("latin-1")
        public_key = self._public_key
        if public_key is None or not verify_signature(
            public_key, signature, timestamp, body
        ):
            await _respond(send, 401)
            return
        received = asyncio.get_running_loop().time()
        kind = decoder(_Kind).decode(body)
        if kind.token is None:
            named = _webhook_event(body)
            if named is not None:
                self.bot._dispatch(0, 0, *named, received)
            await _respond(send, 204)
        elif kind.type == InteractionType.PING:
            await _respond(send, 200, PONG)
        else:
            self.bot._dispatch(0, 0, InteractionCreate.NAME, body, received)
            await _respond(send, 202)


async def _body(receive: Receive) -> bytes | None:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return b"".join(chunks)
        chunk: bytes = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


async def _respond(send: Send, status: int, body: bytes = b"") -> None:
    headers = [(b"content-type", b"application/json")] if body else []
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
