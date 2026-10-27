import asyncio
import os
import subprocess
import sys
import textwrap
import warnings
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import pytest

from lyrecord import _native
from lyrecord._bridge import Bridge
from lyrecord.errors import TransportError

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


async def serve(handler: Handler) -> tuple[asyncio.Server, str]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]
    return server, f"http://{host}:{port}/"


async def reply_hello(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    while True:
        try:
            await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            break
        writer.write(
            b"HTTP/1.1 200 OK\r\nX-Test: yes\r\nContent-Length: 5\r\n\r\nhello"
        )
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

    def drain(self) -> "list[_native.Message]":
        raise RuntimeError("drain exploded")


def test_round_trip_sends_headers_and_body(run: Runner) -> None:
    received: list[bytes] = []

    async def record(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        received.append(head + await reader.readexactly(4))
        writer.write(
            b"HTTP/1.1 201 Created\r\nX-Test: yes\r\nContent-Length: 5\r\n\r\nhello"
        )
        await writer.drain()
        writer.close()

    async def main() -> None:
        server, url = await serve(record)
        async with server, Bridge() as bridge:
            response = await bridge.http(
                "POST", url, headers=[("X-Probe", "1")], body=b"ping"
            )

        assert response.status == 201
        assert ("x-test", "yes") in response.headers
        assert response.body == b"hello"
        assert b"x-probe: 1\r\n" in received[0].lower()
        assert received[0].endswith(b"\r\n\r\nping")

    run(main())


def test_concurrent_requests_all_complete(run: Runner) -> None:
    async def main() -> None:
        server, url = await serve(reply_hello)
        async with server, Bridge() as bridge:
            responses = await asyncio.gather(
                *(bridge.http("GET", url) for _ in range(500))
            )

        assert [response.body for response in responses] == [b"hello"] * 500

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
        async with server, Bridge() as bridge:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.2):
                    await bridge.http("GET", url)
            await asyncio.wait_for(disconnected.wait(), 5)
            assert bridge._core.inflight() == 0

    run(main())


def test_connection_failure_hides_the_url(run: Runner) -> None:
    async def main() -> None:
        server, url = await serve(reply_hello)
        server.close()
        await server.wait_closed()
        async with Bridge() as bridge:
            with pytest.raises(TransportError) as raised:
                await bridge.http("GET", f"{url}webhooks/1/SECRET-TOKEN")

        assert "SECRET-TOKEN" not in str(raised.value)

    run(main())


def test_invalid_request_raises_immediately(run: Runner) -> None:
    async def main() -> None:
        async with Bridge() as bridge:
            with pytest.raises(ValueError, match="invalid method"):
                await bridge.http("GE T", "https://example.com")

    run(main())


def test_requires_a_running_bridge(run: Runner) -> None:
    async def main() -> None:
        bridge = Bridge()
        with pytest.raises(RuntimeError, match="not running"):
            await bridge.http("GET", "https://example.com")
        await bridge.close()

    run(main())


def test_can_only_start_once(run: Runner) -> None:
    async def main() -> None:
        async with Bridge() as bridge:
            with pytest.raises(RuntimeError, match="only be started once"):
                await bridge.__aenter__()

    run(main())


def test_closing_fails_pending_requests(run: Runner) -> None:
    async def main() -> None:
        server, url = await serve(never_reply)
        async with server:
            bridge = Bridge()
            await bridge.__aenter__()
            pending = asyncio.create_task(bridge.http("GET", url))
            await asyncio.sleep(0.1)
            await bridge.close()
            with pytest.raises(TransportError, match="stopped"):
                await pending

    run(main())


def test_delivery_failure_stops_the_bridge(
    run: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def main() -> None:
        server, url = await serve(reply_hello)
        async with server, Bridge() as bridge:
            monkeypatch.setattr(bridge, "_core", FailingDrain(bridge._core))
            with pytest.raises(TransportError) as raised:
                await bridge.http("GET", url)
            assert isinstance(raised.value.__cause__, RuntimeError)
            with pytest.raises(RuntimeError, match="stopped"):
                await bridge.http("GET", url)

    run(main())


def test_rejects_use_from_another_loop(run: Runner) -> None:
    async def main() -> None:
        async with Bridge() as bridge:

            def use_from_another_loop() -> None:
                asyncio.run(bridge.http("GET", "http://127.0.0.1:1/"))

            with pytest.raises(RuntimeError, match="another event loop"):
                await asyncio.to_thread(use_from_another_loop)

    run(main())


def test_close_propagates_the_callers_cancellation() -> None:
    async def main() -> None:
        bridge = Bridge()
        await bridge.__aenter__()
        closing = asyncio.create_task(bridge.close())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing

    asyncio.run(main(), loop_factory=LoopWithoutReaders)


@pytest.mark.skipif(sys.platform != "linux", reason="fork is only reliable on Linux")
def test_forked_child_refuses_to_start() -> None:
    bridge = Bridge()
    read_end, write_end = os.pipe()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        try:
            Bridge()
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
    asyncio.run(bridge.close())


def test_process_exits_cleanly_with_requests_in_flight() -> None:
    script = textwrap.dedent(
        """
        import asyncio, os, socket
        from lyrecord._bridge import Bridge

        async def main():
            listener = socket.create_server(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{listener.getsockname()[1]}/"
            bridge = Bridge()
            await bridge.__aenter__()
            for _ in range(10):
                asyncio.create_task(bridge.http("GET", url))
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
