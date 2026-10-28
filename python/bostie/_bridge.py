import asyncio
import logging
import socket
import weakref
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

from bostie import _native
from bostie.errors import (
    BostieError,
    InvalidToken,
    RequestBudgetExhausted,
    TransportError,
)

BELL_READ_SIZE = 4096
FAILURE_ERRORS = {
    "transport": TransportError,
    "invalid_token": InvalidToken,
    "request_budget": RequestBudgetExhausted,
    "bug": BostieError,
}

type StatusDelivery = (
    _native.DisconnectedDelivery
    | _native.StoppedDelivery
    | _native.BackpressureDelivery
    | _native.InvalidRequestsDelivery
)
type GatewayDelivery = _native.DispatchDelivery | StatusDelivery
type OnGateway = Callable[[GatewayDelivery, float], object]

log = logging.getLogger("bostie.bridge")


class Bridge:
    def __init__(
        self, token: str, api_url: str, on_gateway: OnGateway, *, global_limit: int
    ) -> None:
        self.core, bell_handle = _native.Core.create(token, api_url, global_limit)
        self._on_gateway = on_gateway
        self._bell = socket.socket(fileno=bell_handle)
        self._bell.setblocking(False)
        self._waiting: dict[int, asyncio.Future[tuple[int, bytes]]] = {}
        self._futures: weakref.WeakSet[asyncio.Future[Any]] = weakref.WeakSet()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fallback_pump: asyncio.Task[None] | None = None
        self._stopped = False
        self._failure: BaseException | None = None

    async def __aenter__(self) -> Self:
        if self._loop is not None:
            raise RuntimeError("a bridge can only be started once")
        self._loop = asyncio.get_running_loop()
        try:
            self._loop.add_reader(self._bell, self._on_bell)
        except NotImplementedError:
            self._fallback_pump = self._loop.create_task(
                self._pump_bell(), name="bostie-bell"
            )
            self._fallback_pump.add_done_callback(self._close_bell)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("the bridge belongs to another event loop")
        self._stop(None)
        if self._fallback_pump is not None:
            await asyncio.wait([self._fallback_pump])

    async def request(
        self,
        method: str,
        route: str,
        params: dict[str, str],
        query: list[tuple[str, str]],
        body: bytes | None = None,
        content_type: str | None = None,
        reason: str | None = None,
    ) -> tuple[int, bytes]:
        loop = self._running_loop()
        ticket = self.core.request(
            method, route, params, query, body, content_type, reason
        )
        future: asyncio.Future[tuple[int, bytes]] = loop.create_future()
        self._waiting[ticket] = future
        try:
            return await future
        finally:
            if self._waiting.pop(ticket, None) is not None:
                self.core.cancel(ticket)

    def create_future(self) -> asyncio.Future[Any]:
        future = self._running_loop().create_future()
        self._futures.add(future)
        return future

    def _running_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            raise RuntimeError(
                "the bridge is not running; use 'async with Bridge(...)'"
            )
        if self._stopped:
            raise RuntimeError("the bridge stopped") from self._failure
        if loop is not self._loop:
            raise RuntimeError("the bridge belongs to another event loop")
        return loop

    def _on_bell(self) -> None:
        try:
            woke = self._bell.recv(BELL_READ_SIZE)
        except BlockingIOError:
            return
        except OSError as error:
            self._stop(error)
            return
        if not woke:
            self._stop(TransportError("the core closed the bridge"))
            return
        try:
            self._deliver_ready()
        except Exception as error:
            self._stop(error)

    async def _pump_bell(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while await loop.sock_recv(self._bell, BELL_READ_SIZE):
                self._deliver_ready()
            self._stop(TransportError("the core closed the bridge"))
        except Exception as error:
            self._stop(error)

    def _close_bell(self, pump: asyncio.Task[None]) -> None:
        self._bell.close()

    def _deliver_ready(self) -> None:
        deliveries = self.core.drain()
        received = asyncio.get_running_loop().time()
        for delivery in deliveries:
            match delivery:
                case ("response", ticket, status, body):
                    self._resolve(ticket, (status, body))
                case ("failure", ticket, kind, detail):
                    self._reject(ticket, FAILURE_ERRORS[kind](detail))
                case (
                    "voice_state"
                    | "voice_moved"
                    | "voice_dave"
                    | "voice_self"
                    | "voice_speaking"
                    | "voice_clients"
                    | "voice_client_left"
                    | "voice_stalled"
                    | "voice_wake"
                    | "voice_marker",
                    *_,
                ):
                    pass
                case _:
                    try:
                        self._on_gateway(delivery, received)
                    except Exception:
                        log.exception("handling a %r delivery failed", delivery[0])

    def _resolve(self, ticket: int, response: tuple[int, bytes]) -> None:
        future = self._waiting.pop(ticket, None)
        if future is not None and not future.done():
            future.set_result(response)

    def _reject(self, ticket: int, error: BaseException) -> None:
        future = self._waiting.pop(ticket, None)
        if future is not None and not future.done():
            future.set_exception(error)

    def _stop(self, failure: BaseException | None) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._failure = failure
        pending = [*self._waiting.values(), *self._futures]
        self._waiting.clear()
        for future in pending:
            if not future.done():
                error = TransportError("the bridge stopped")
                error.__cause__ = failure
                future.set_exception(error)
        if self._fallback_pump is None:
            if self._loop is not None:
                self._loop.remove_reader(self._bell)
            self._bell.close()
        elif self._fallback_pump is not asyncio.current_task():
            self._fallback_pump.cancel()
        self.core.close()
