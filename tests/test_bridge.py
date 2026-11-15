import asyncio
import logging
import os
import subprocess
import sys
import textwrap
import warnings
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, override

import pytest

from bostie import _native
from bostie._bridge import Bridge, GatewayDelivery
from bostie.errors import (
    BostieError,
    InvalidToken,
    RequestBudgetExhausted,
    StateError,
    TransportError,
)
from bostie.gateway import Shards
from bostie.http import HTTPSettings
from bostie.testing import FakeDiscord

TOKEN = "dummy-token"
API_URL = "https://discord.test/api/v10"
CALLBACK = "/interactions/{interaction_id}/{interaction_token}/callback"
OK = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"

type Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]
type Runner = Callable[[Coroutine[Any, Any, None]], None]
type Rewrite = Callable[[list[_native.Delivery]], list[_native.Delivery]]


class LoopWithoutReaders(asyncio.SelectorEventLoop):
    @override
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


class RewrittenCore:
    def __init__(self, core: _native.Core, rewrite: Rewrite) -> None:
        self.core = core
        self.rewrite = rewrite

    def __getattr__(self, name: str) -> Any:
        return getattr(self.core, name)

    def drain(self) -> "list[_native.Delivery]":
        return self.rewrite(self.core.drain())


def test_concurrent_requests_all_complete(run: Runner) -> None:
    async def main() -> None:
        server, url = await serve(reply_hello)
        async with server, bridge(url) as running:
            responses = await asyncio.gather(
                *(callback(running, index) for index in range(500))
            )

        assert responses == [(200, b"hello")] * 500

    run(main())


def test_cancelling_a_request_drops_its_connection(run: Runner) -> None:
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


def test_a_proxy_bostie_cannot_tunnel_through_is_refused() -> None:
    with pytest.raises(ValueError, match="http:// URL"):
        Bridge(TOKEN, API_URL, ignore, http=HTTPSettings(proxy="socks5://proxy:1080"))


def test_closing_fails_pending_requests_and_futures(run: Runner) -> None:
    async def main() -> None:
        accepted = asyncio.Event()
        disconnected = asyncio.Event()

        async def hold(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            accepted.set()
            await never_reply(reader, writer)
            disconnected.set()

        server, url = await serve(hold)
        async with server:
            running = bridge(url)
            await running.__aenter__()
            pending = asyncio.create_task(me(running))
            stopped = running.create_future()
            await accepted.wait()
            await running.close()
            for waiter in (pending, stopped):
                with pytest.raises(TransportError, match="stopped"):
                    await waiter
            await disconnected.wait()

    run(main())


def test_delivery_failure_stops_the_bridge(
    run: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(deliveries: "list[_native.Delivery]") -> "list[_native.Delivery]":
        raise RuntimeError("drain exploded")

    async def main() -> None:
        server, url = await serve(reply_hello)
        async with server, bridge(url) as running:
            monkeypatch.setattr(running, "core", RewrittenCore(running.core, explode))
            with pytest.raises(TransportError) as raised:
                await me(running)
            assert isinstance(raised.value.__cause__, RuntimeError)
            with pytest.raises(StateError, match="stopped"):
                await me(running)

    run(main())


def test_a_failing_callback_is_logged_and_the_bridge_keeps_working(
    run: Runner, caplog: pytest.LogCaptureFixture
) -> None:
    async def main() -> None:
        exploded = asyncio.Event()

        def explode(delivery: GatewayDelivery, received: float) -> None:
            exploded.set()
            raise ValueError("handler exploded")

        async with FakeDiscord() as fake:
            async with Bridge(fake.token, fake.api_url, explode) as running:
                gateway = Shards(
                    running, fake.token, intents=513, shard_count=1, shard_ids=[0]
                )
                started = asyncio.ensure_future(gateway.start(fake.gateway_url, {}))
                await exploded.wait()
                assert (await me(running))[0] == 200
            with pytest.raises(TransportError, match="stopped"):
                await started

    run(main())

    failures = {
        (record.levelno, str(record.exc_info[1]))
        for record in caplog.records
        if record.name == "bostie.bridge" and record.exc_info is not None
    }
    assert failures == {(logging.ERROR, "handler exploded")}


def test_rust_logs_reach_the_python_logger_at_their_level(
    run: Runner, caplog: pytest.LogCaptureFixture
) -> None:
    async def main() -> None:
        async with (
            FakeDiscord() as fake,
            Bridge(fake.token, fake.api_url, ignore) as running,
        ):
            fake.rate_limit("GET", "/users/@me", scope="user", retry_after=0)
            assert (await me(running))[0] == 200

    with caplog.at_level(logging.DEBUG, logger="bostie.rest"):
        run(main())

    logged = [
        (record.levelno, record.getMessage().split()[0])
        for record in caplog.records
        if record.name == "bostie.rest"
        and record.getMessage().startswith(("rate limited ", "retrying "))
    ]
    assert logged == [(logging.WARNING, "rate"), (logging.DEBUG, "retrying")]


def test_rejects_use_from_another_loop(run: Runner) -> None:
    async def main() -> None:
        async with bridge() as running:

            def use_from_another_loop() -> None:
                asyncio.run(me(running))

            with pytest.raises(StateError, match="another event loop"):
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
        ("bug", BostieError),
    ],
)
def test_failure_kinds_map_to_their_exceptions(
    run: Runner,
    monkeypatch: pytest.MonkeyPatch,
    kind: "_native.FailureKind",
    error_type: type[BostieError],
) -> None:
    def relabel(deliveries: "list[_native.Delivery]") -> "list[_native.Delivery]":
        return [("failure", delivery[1], kind, "relabelled") for delivery in deliveries]

    async def main() -> None:
        server, url = await serve(reply_hello)
        async with server, bridge(url) as running:
            monkeypatch.setattr(running, "core", RewrittenCore(running.core, relabel))
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
        from bostie._bridge import Bridge

        ROUTE = "/interactions/{interaction_id}/{interaction_token}/callback"

        async def main():
            listener = socket.create_server(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{listener.getsockname()[1]}/api/v10"
            bridge = Bridge(
                "dummy-token", url, lambda delivery, received: None
            )
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
        result = subprocess.run(
            [sys.executable, "-X", "dev", "-c", script],
            env={**os.environ, "EXIT_MODE": exit_mode},
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode()
