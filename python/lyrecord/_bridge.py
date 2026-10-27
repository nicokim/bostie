import asyncio
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import Self, assert_never

from lyrecord import _native
from lyrecord.errors import TransportError

BELL_READ_SIZE = 4096


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: list[tuple[str, str]]
    body: bytes


class Bridge:
    def __init__(self) -> None:
        self._core, bell_handle = _native.Core.create()
        self._bell = socket.socket(fileno=bell_handle)
        self._bell.setblocking(False)
        self._waiting: dict[int, asyncio.Future[HttpResponse]] = {}
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
                self._pump_bell(), name="lyrecord-bell"
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

    async def http(
        self,
        method: str,
        url: str,
        *,
        headers: Iterable[tuple[str, str]] = (),
        body: bytes | None = None,
    ) -> HttpResponse:
        loop = self._running_loop()
        ticket = self._core.submit_http(method, url, list(headers), body)
        future: asyncio.Future[HttpResponse] = loop.create_future()
        self._waiting[ticket] = future
        try:
            return await future
        finally:
            if self._waiting.pop(ticket, None) is not None:
                self._core.cancel(ticket)

    def _running_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            raise RuntimeError("the bridge is not running; use 'async with Bridge()'")
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
        for message in self._core.drain():
            self._deliver(message)

    def _deliver(self, message: "_native.Message") -> None:
        match message:
            case ("response", ticket, status, headers, body):
                self._resolve(ticket, HttpResponse(status, headers, body))
            case ("failure", ticket, detail):
                self._reject(ticket, TransportError(detail))
            case _:
                assert_never(message)

    def _resolve(self, ticket: int, response: HttpResponse) -> None:
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
        for ticket in list(self._waiting):
            error = TransportError("the bridge stopped")
            error.__cause__ = failure
            self._reject(ticket, error)
        if self._fallback_pump is None:
            if self._loop is not None:
                self._loop.remove_reader(self._bell)
            self._bell.close()
        elif self._fallback_pump is not asyncio.current_task():
            self._fallback_pump.cancel()
        self._core.close()
