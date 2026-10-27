import asyncio
import logging
import os
import subprocess
import sys
import textwrap
import warnings
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import pytest

from lyrecord import _native
from lyrecord._bridge import Bridge, GatewayDelivery
from lyrecord.errors import (
    InvalidToken,
    LyrecordError,
    RequestBudgetExhausted,
    TransportError,
)

TOKEN = "dummy-token"
API_URL = "https://discord.test/api/v10"
CALLBACK = "/interactions/{interaction_id}/{interaction_token}/callback"
OK = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"

type Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]
type Runner = Callable[[Coroutine[Any, Any, None]], None]


class LoopWithoutReaders(asyncio.SelectorEventLoop):
    def add_reader(
        self, fd: object, callback: Callable[..., object], *args: object
    ) -> None:
        raise NotImplementedError


@pytest.fixture(params=["reader", "pump"])
def run(request: pytest.FixtureRequest) -> Runner:
    factory = (
        asyncio.new_event_loop if request.param == "reader" else LoopWithoutReaders
    )
    return lambda main: asyncio.run(main, loop_factory=factory)


def ignore(delivery: GatewayDelivery, received: float) -> None:
    pass


def bridge(api_url: str = API_URL) -> Bridge:
    return Bridge(TOKEN, api_url, ignore)


async def me(bridge: Bridge) -> tuple[int, bytes]:
    return await bridge.request("GET", "/users/@me", {}, [])


async def callback(bridge: Bridge, interaction_id: int) -> tuple[int, bytes]:
    params = {"interaction_id": str(interaction_id), "interaction_token": "token"}
    return await bridge.request("POST", CALLBACK, params, [])


async def serve(handler: Handler) -> tuple[asyncio.Server, str]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]
    return server, f"http://{host}:{port}/api/v10"


async def reply_hello(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    while True:
        try:
            await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            break
        writer.write(OK)
        await writer.drain()
    writer.close()


async def never_reply(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    await reader.read()
    writer.close()


class FailingDrain:
    def __init__(self, core: _native.Core) -> None:
        self._core = core

    def __getattr__(self, name: str) -> Any:
        return getattr(self._core, name)

    def drain(self) -> "list[_native.Delivery]":
        raise RuntimeError("drain exploded")


class RelabelledFailures:
    def __init__(self, core: _native.Core, kind: "_native.FailureKind") -> None:
        self._core = core
        self._kind = kind

    def __getattr__(self, name: str) -> Any:
        return getattr(self._core, name)

    def drain(self) -> "list[_native.Delivery]":
        return [
            ("failure", delivery[1], self._kind, "relabelled")
            for delivery in self._core.drain()
        ]


class InjectedOnce:
    def __init__(self, core: _native.Core, injected: list[GatewayDelivery]) -> None:
        self._core = core
        self._injected = injected

    def __getattr__(self, name: str) -> Any:
        return getattr(self._core, name)

    def drain(self) -> "list[_native.Delivery]":
        injected, self._injected = self._injected, []
        return [*injected, *self._core.drain()]


def test_round_trip_sends_the_route_headers_and_body(run: Runner) -> None:
    received: list[bytes] = []

    async def record(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        received.append(head + await reader.readexactly(4))
        writer.write(b"HTTP/1.1 201 Created\r\nContent-Length: 5\r\n\r\nhello")
        await writer.drain()
        writer.close()

    async def main() -> None:
        server, url = await serve(record)
        async with server, bridge(url) as running:
            response = await running.request(
                "POST",
                "/channels/{channel_id}/messages",
                {"channel_id": "7"},
                [("tts", "true")],
                b"ping",
                "text/plain",
                "spam",
            )

        assert response == (201, b"hello")
        head = received[0].lower()
        assert head.startswith(b"post /api/v10/channels/7/messages?tts=true ")
        assert b"\r\nauthorization: bot dummy-token\r\n" in head
        assert b"\r\ncontent-type: text/plain\r\n" in head
        assert b"\r\nx-audit-log-reason: spam\r\n" in head
        assert head.endswith(b"\r\n\r\nping")

    run(main())


def test_concurrent_requests_all_complete(run: Runner) -> None:
    async def main() -> None:
        server, url = await serve(reply_hello)
        async with server, bridge(url) as running:
            responses = await asyncio.gather(
                *(callback(running, index) for index in range(500))
            )

        assert responses == [(200, b"hello")] * 500

    run(main())


def test_cancellation_aborts_the_rust_task(run: Runner) -> None:
    async def main() -> None:
        disconnected = asyncio.Event()

        async def wait_for_disconnect(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await never_reply(reader, writer)
            disconnected.set()

        server, url = await serve(wait_for_disconnect)
        async with server, bridge(url) as running:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.2):
                    await me(running)
            await asyncio.wait_for(disconnected.wait(), 5)
            assert running.core.inflight() == 0

    run(main())


def test_connection_failure_hides_the_url(run: Runner) -> None:
    async def main() -> None:
        server, url = await serve(reply_hello)
        server.close()
        await server.wait_closed()
        async with bridge(url) as running:
            with pytest.raises(TransportError) as raised:
                await running.request(
                    "POST",
                    "/webhooks/{webhook_id}/{webhook_token}",
                    {"webhook_id": "1", "webhook_token": "SECRET-TOKEN"},
                    [],
                )

        assert "SECRET-TOKEN" not in str(raised.value)

    run(main())


def test_invalid_request_raises_immediately(run: Runner) -> None:
    async def main() -> None:
        async with bridge() as running:
            with pytest.raises(ValueError, match="unsupported method"):
                await running.request("GE T", "/users/@me", {}, [])
            with pytest.raises(ValueError, match="needs the param"):
                await running.request("GET", "/channels/{channel_id}", {}, [])
            with pytest.raises(ValueError, match="webhook_token") as raised:
                await running.request(
                    "POST",
                    "/webhooks/{webhook_id}/{webhook_token}",
                    {"webhook_id": "1", "webhook_token": "SECRET-TOKEN\n"},
                    [],
                )
            assert "SECRET-TOKEN" not in str(raised.value)

    run(main())


def test_requires_a_running_bridge(run: Runner) -> None:
    async def main() -> None:
        idle = bridge()
        with pytest.raises(RuntimeError, match="not running"):
            await me(idle)
        await idle.close()

    run(main())


def test_can_only_start_once(run: Runner) -> None:
    async def main() -> None:
        async with bridge() as running:
            with pytest.raises(RuntimeError, match="only be started once"):
                await running.__aenter__()

    run(main())


def test_closing_fails_pending_requests_and_futures(run: Runner) -> None:
    async def main() -> None:
        server, url = await serve(never_reply)
        async with server:
            running = bridge(url)
            await running.__aenter__()
            pending = asyncio.create_task(me(running))
            stopped = running.create_future()
            await asyncio.sleep(0.1)
            await running.close()
            for waiter in (pending, stopped):
                with pytest.raises(TransportError, match="stopped"):
                    await waiter

    run(main())


def test_delivery_failure_stops_the_bridge(
    run: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def main() -> None:
        server, url = await serve(reply_hello)
        async with server, bridge(url) as running:
            monkeypatch.setattr(running, "core", FailingDrain(running.core))
            with pytest.raises(TransportError) as raised:
                await me(running)
            assert isinstance(raised.value.__cause__, RuntimeError)
            with pytest.raises(RuntimeError, match="stopped"):
                await me(running)

    run(main())


def test_gateway_deliveries_reach_the_callback_with_the_drain_time(
    run: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    injected: list[GatewayDelivery] = [
        ("disconnected", 0, 4000, "unknown error", True),
        ("invalid_requests", 1000),
    ]
    handed: list[tuple[GatewayDelivery, float]] = []

    def record(delivery: GatewayDelivery, received: float) -> None:
        handed.append((delivery, received))

    async def main() -> None:
        server, url = await serve(reply_hello)
        loop = asyncio.get_running_loop()
        async with server, Bridge(TOKEN, url, record) as running:
            monkeypatch.setattr(running, "core", InjectedOnce(running.core, injected))
            before = loop.time()
            assert await me(running) == (200, b"hello")
            after = loop.time()

        assert [delivery for delivery, _ in handed] == injected
        first, second = (received for _, received in handed)
        assert before <= first == second <= after

    run(main())


def test_a_failing_callback_is_logged_and_the_bridge_keeps_working(
    run: Runner, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def explode(delivery: GatewayDelivery, received: float) -> None:
        raise ValueError("handler exploded")

    async def main() -> None:
        server, url = await serve(reply_hello)
        async with server, Bridge(TOKEN, url, explode) as running:
            injected: list[GatewayDelivery] = [
                ("backpressure", 0, 1),
                ("backpressure", 0, 2),
            ]
            monkeypatch.setattr(running, "core", InjectedOnce(running.core, injected))
            assert await me(running) == (200, b"hello")
            assert await me(running) == (200, b"hello")

    run(main())

    failures = [record for record in caplog.records if record.name == "lyrecord.bridge"]
    assert [record.levelno for record in failures] == [logging.ERROR] * 2
    assert all(record.exc_info is not None for record in failures)


def test_rejects_use_from_another_loop(run: Runner) -> None:
    async def main() -> None:
        async with bridge() as running:

            def use_from_another_loop() -> None:
                asyncio.run(me(running))

            with pytest.raises(RuntimeError, match="another event loop"):
                await asyncio.to_thread(use_from_another_loop)

    run(main())


def test_close_propagates_the_callers_cancellation() -> None:
    async def main() -> None:
        running = bridge()
        await running.__aenter__()
        closing = asyncio.create_task(running.close())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing

    asyncio.run(main(), loop_factory=LoopWithoutReaders)


@pytest.mark.parametrize(
    ("kind", "error_type"),
    [
        ("transport", TransportError),
        ("invalid_token", InvalidToken),
        ("request_budget", RequestBudgetExhausted),
        ("bug", LyrecordError),
    ],
)
def test_failure_kinds_map_to_their_exceptions(
    run: Runner,
    monkeypatch: pytest.MonkeyPatch,
    kind: "_native.FailureKind",
    error_type: type[LyrecordError],
) -> None:
    async def main() -> None:
        server, url = await serve(reply_hello)
        async with server, bridge(url) as running:
            monkeypatch.setattr(running, "core", RelabelledFailures(running.core, kind))
            with pytest.raises(error_type, match="relabelled") as raised:
                await me(running)

        assert type(raised.value) is error_type

    run(main())


@pytest.mark.skipif(sys.platform != "linux", reason="fork is only reliable on Linux")
def test_forked_child_refuses_to_start() -> None:
    parent = bridge()
    read_end, write_end = os.pipe()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        try:
            bridge()
        except RuntimeError as error:
            os.write(
                write_end,
                b"refused" if "forked child" in str(error) else b"wrong error",
            )
        else:
            os.write(write_end, b"started")
        os._exit(0)

    os.close(write_end)
    os.waitpid(pid, 0)
    assert os.read(read_end, 64) == b"refused"
    os.close(read_end)
    asyncio.run(parent.close())


def test_process_exits_cleanly_with_requests_in_flight() -> None:
    script = textwrap.dedent(
        """
        import asyncio, os, socket
        from lyrecord._bridge import Bridge

        ROUTE = "/interactions/{interaction_id}/{interaction_token}/callback"

        async def main():
            listener = socket.create_server(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{listener.getsockname()[1]}/api/v10"
            bridge = Bridge("dummy-token", url, lambda delivery, received: None)
            await bridge.__aenter__()
            for index in range(10):
                params = {"interaction_id": str(index), "interaction_token": "t"}
                asyncio.create_task(bridge.request("POST", ROUTE, params, []))
            await asyncio.sleep(0.05)
            if os.environ["EXIT_MODE"] == "hard":
                os._exit(0)

        asyncio.run(main())
        """
    )
    for exit_mode in ("normal", "hard"):
        for _ in range(10):
            result = subprocess.run(
                [sys.executable, "-X", "dev", "-c", script],
                env={**os.environ, "EXIT_MODE": exit_mode},
                capture_output=True,
                timeout=10,
                check=False,
            )
            assert result.returncode == 0, result.stderr.decode()
