import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from types import MappingProxyType
from typing import Any

import msgspec
import pytest
from fakediscord import FakeDiscord, payloads

from lyrecord._bridge import Bridge, GatewayDelivery, StatusDelivery
from lyrecord.errors import (
    GatewayClosed,
    InvalidToken,
    PrivilegedIntentsRequired,
    ShardingRequired,
    TransportError,
)
from lyrecord.gateway import GatewayClient

type Scenario = Callable[["Harness"], Coroutine[Any, Any, None]]


class Harness:
    def __init__(self, fake: FakeDiscord, token: str) -> None:
        self.fake = fake
        self.deliveries: asyncio.Queue[GatewayDelivery] = asyncio.Queue()
        self.bridge = Bridge(token, fake.api_url, self._on_gateway, global_limit=50)
        self.gateway = GatewayClient(self.bridge, token)

    def _on_gateway(self, delivery: GatewayDelivery, received: float) -> None:
        self.deliveries.put_nowait(delivery)
        match delivery:
            case ("dispatch", _, _, _, _):
                pass
            case _:
                self.gateway.handle(delivery)

    def start(self, **identify: Any) -> "asyncio.Task[None]":
        return asyncio.create_task(
            self.gateway.start(self.fake.gateway_url, intents=513, **identify)
        )

    async def next_dispatch(self) -> tuple[int, str, Any]:
        while True:
            match await self.deliveries.get():
                case ("dispatch", _, seq, name, data):
                    return seq, name, msgspec.json.decode(data)

    async def ready(self, **identify: Any) -> "asyncio.Task[None]":
        started = self.start(**identify)
        await self.fake.next_identify()
        await self.next_dispatch()
        return started


def run(scenario: Scenario, token: str | None = None, **options: Any) -> None:
    async def main() -> None:
        async with FakeDiscord(**options) as fake:
            harness = Harness(fake, token or fake.token)
            async with harness.bridge, asyncio.timeout(10):
                await scenario(harness)
                await harness.gateway.close()

    asyncio.run(main())


async def sent_op(fake: FakeDiscord, op: int) -> Any:
    while (payload := await fake.gateway_received.get())["op"] != op:
        pass
    return payload["d"]


def test_start_identifies_delivers_dispatches_and_closes_with_1000() -> None:
    async def scenario(harness: Harness) -> None:
        fake, gateway = harness.fake, harness.gateway
        assert gateway.latency is None
        gateway.identify_limit({"remaining": 0, "total": 1, "reset_after": 300})
        started = harness.start(presence=MappingProxyType({"status": "idle"}))

        assert await harness.deliveries.get() == (
            "disconnected",
            0,
            None,
            "daily identify limit reached; waiting 1 s",
            False,
        )
        identify = await fake.next_identify()
        assert identify == {
            "token": fake.token,
            "intents": 513,
            "properties": {
                "os": sys.platform,
                "browser": "lyrecord",
                "device": "lyrecord",
            },
            "shard": [0, 1],
            "presence": {"status": "idle"},
        }
        seq, name, ready = await harness.next_dispatch()
        assert (seq, name, ready["user"]) == (1, "READY", fake.bot_user)

        message = payloads.message()
        seq = await fake.dispatch("MESSAGE_CREATE", message)
        assert await harness.next_dispatch() == (seq, "MESSAGE_CREATE", message)

        presence: dict[str, Any] = {
            "since": None,
            "activities": [],
            "status": "dnd",
            "afk": False,
        }
        gateway.send(3, MappingProxyType(presence))
        assert await sent_op(fake, 3) == presence

        for _ in range(2):
            await sent_op(fake, 1)
        assert gateway.latency is not None
        await gateway.close()
        await started
        assert fake.connections[-1].close_code == 1000
        assert gateway.latency is None

    run(scenario, heartbeat_interval=50)


def test_start_on_a_bridge_that_is_not_running_starts_no_shard() -> None:
    async def main() -> None:
        async with FakeDiscord() as fake:
            harness = Harness(fake, fake.token)
            with pytest.raises(RuntimeError, match="not running"):
                await harness.gateway.start(fake.gateway_url, intents=513)

            async with harness.bridge, asyncio.timeout(10):
                started = await harness.ready()
                await harness.gateway.close()
                await started

    asyncio.run(main())


def test_close_ends_start_even_when_the_close_is_not_echoed_in_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lyrecord.gateway.CLOSE_TIMEOUT", 0)

    async def scenario(harness: Harness) -> None:
        started = await harness.ready()

        await harness.gateway.close()
        await harness.bridge.close()

        await started

    run(scenario)


def test_close_after_start_is_cancelled_still_closes_with_1000() -> None:
    async def scenario(harness: Harness) -> None:
        started = await harness.ready()
        started.cancel()
        with pytest.raises(asyncio.CancelledError):
            await started

        await harness.gateway.close()

        assert harness.fake.connections[-1].close_code == 1000

    run(scenario)


def test_the_filter_drops_dispatches_nobody_listens_to() -> None:
    async def scenario(harness: Harness) -> None:
        harness.gateway.filter(["MESSAGE_CREATE"])
        await harness.ready()

        await harness.fake.dispatch("TYPING_START", {"channel_id": "1", "user_id": "2"})
        seq = await harness.fake.dispatch("MESSAGE_CREATE", payloads.message())

        assert (await harness.next_dispatch())[:2] == (seq, "MESSAGE_CREATE")

    run(scenario)


def test_a_dropped_connection_resumes_and_replays_missed_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="lyrecord.gateway")

    async def scenario(harness: Harness) -> None:
        fake = harness.fake
        harness.start()
        await fake.next_identify()
        _, _, ready = await harness.next_dispatch()

        fake.drop_gateway()
        seq = await fake.dispatch("MESSAGE_CREATE", payloads.message())
        resume = await fake.next_resume()
        disconnected = await harness.deliveries.get()
        replayed = [(await harness.next_dispatch())[:2] for _ in range(2)]

        assert resume == {
            "token": fake.token,
            "session_id": ready["session_id"],
            "seq": 1,
        }
        assert disconnected[:2] == ("disconnected", 0)
        assert disconnected[-1] is True
        assert replayed == [(seq, "MESSAGE_CREATE"), (seq + 1, "RESUMED")]

        harness.gateway.reconnect()
        assert (await fake.next_resume())["seq"] == seq + 1
        assert (await harness.next_dispatch())[1] == "RESUMED"
        assert fake.connections[-1].path == "/gateway-resume"

    run(scenario)

    disconnects = [
        record for record in caplog.records if "disconnected" in record.getMessage()
    ]
    assert [record.levelno for record in disconnects] == [logging.INFO] * 2


@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        (4014, PrivilegedIntentsRequired),
        (4011, ShardingRequired),
        (4010, GatewayClosed),
    ],
)
def test_a_fatal_close_code_raises_its_error_from_start(
    code: int, error_type: type[GatewayClosed]
) -> None:
    async def scenario(harness: Harness) -> None:
        started = await harness.ready()

        await harness.fake.close_gateway(code)

        with pytest.raises(GatewayClosed) as raised:
            await started
        assert type(raised.value) is error_type
        assert raised.value.code == code

    run(scenario)


def test_a_rejected_identify_token_raises_invalid_token() -> None:
    async def scenario(harness: Harness) -> None:
        with pytest.raises(InvalidToken, match="4004"):
            await harness.start()

    run(scenario, token="wrong-token")


def test_closing_the_bridge_fails_start_and_drops_the_connection() -> None:
    async def scenario(harness: Harness) -> None:
        started = await harness.ready()

        await harness.bridge.close()

        with pytest.raises(TransportError, match="stopped"):
            await started
        await asyncio.wait_for(harness.fake.connections[-1].request.task, 1)

    run(scenario)


def test_close_code_4002_resumes_and_is_reported_as_a_lyrecord_bug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(harness: Harness) -> None:
        await harness.ready()

        await harness.fake.close_gateway(4002)

        await harness.fake.next_resume()
        assert (await harness.next_dispatch())[1] == "RESUMED"

    run(scenario)

    hints = [record for record in caplog.records if "lyrecord bug" in record.message]
    assert [record.levelno for record in hints] == [logging.WARNING]
    assert "close code 4002 (decode error)" in hints[0].message


@pytest.mark.parametrize(
    ("delivery", "level"),
    [
        (("disconnected", 0, None, "reconnect requested", True), logging.INFO),
        (("disconnected", 0, 4009, "session timed out", False), logging.WARNING),
        (("backpressure", 0, 70_000_000), logging.WARNING),
        (("invalid_requests", 1000), logging.WARNING),
    ],
)
def test_status_deliveries_are_logged_at_their_level(
    caplog: pytest.LogCaptureFixture, delivery: StatusDelivery, level: int
) -> None:
    caplog.set_level(logging.INFO, logger="lyrecord.gateway")
    bridge = Bridge(
        "token", "http://127.0.0.1:1/api/v10", lambda *_: None, global_limit=50
    )

    GatewayClient(bridge, "token").handle(delivery)
    asyncio.run(bridge.close())

    assert [record.levelno for record in caplog.records] == [level]
