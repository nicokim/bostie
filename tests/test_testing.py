import ast
import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import aiohttp
import msgspec
import pytest

import bostie.testing
from bostie.testing import FakeDiscord, payloads
from bostie.testing.payloads import Json
from bostie.testing.server import ROUTE_LIMIT

type Scenario = Callable[
    [FakeDiscord, aiohttp.ClientSession], Coroutine[Any, Any, None]
]
type Socket = aiohttp.ClientWebSocketResponse


def run(scenario: Scenario, **options: Any) -> None:
    async def main() -> None:
        async with FakeDiscord(**options) as fake, aiohttp.ClientSession() as session:
            async with asyncio.timeout(5):
                await scenario(fake, session)

    asyncio.run(main())


def auth(fake: FakeDiscord) -> dict[str, str]:
    return {"Authorization": f"Bot {fake.token}"}


async def receive(ws: Socket) -> Any:
    return msgspec.json.decode(await ws.receive_str())


def identify(fake: FakeDiscord, **changes: Any) -> Json:
    d = {"token": fake.token, "intents": 513, "properties": {}} | changes
    return {"op": 2, "d": d}


def resume(fake: FakeDiscord, session_id: str, **changes: Any) -> Json:
    d = {"token": fake.token, "session_id": session_id, "seq": 2} | changes
    return {"op": 6, "d": d}


async def handshake(fake: FakeDiscord, ws: Socket) -> Json:
    await ws.receive_str()
    await ws.send_json(identify(fake))
    ready: Json = await receive(ws)
    return ready


async def close_code(
    session: aiohttp.ClientSession, url: str, *sent: Json | str
) -> int:
    async with session.ws_connect(url) as ws:
        for payload in sent:
            if isinstance(payload, str):
                await ws.send_str(payload)
            else:
                await ws.send_json(payload)
        while (message := await ws.receive()).type is aiohttp.WSMsgType.TEXT:
            pass
    code: int = message.data
    return code


async def received_ops(fake: FakeDiscord, count: int) -> list[int]:
    return [(await fake.gateway_received.get())["op"] for _ in range(count)]


def test_handshake_sends_wire_ordered_frames_and_acks_until_held() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with session.ws_connect(fake.gateway_url) as ws:
            hello = await ws.receive_str()
            await ws.send_json(identify(fake))
            ready = await receive(ws)
            await ws.send_json({"op": 1, "d": 1})
            ack = await ws.receive_str()
            fake.hold_acks()
            await ws.send_json({"op": 1, "d": 1})
            assert await received_ops(fake, 3) == [2, 1, 1]
            seq = await fake.dispatch("MESSAGE_CREATE", payloads.message())
            after_held_beat = await receive(ws)

        assert hello.startswith(
            '{"t":null,"s":null,"op":10,"d":{"heartbeat_interval":41250,'
        )
        assert (ready["t"], ready["s"], ready["op"]) == ("READY", 1, 0)
        assert ready["d"]["user"] == fake.bot_user
        assert ready["d"]["resume_gateway_url"] == fake.gateway_url + "-resume"
        assert (await fake.next_identify())["intents"] == 513
        assert ack == '{"t":null,"s":null,"op":11,"d":null}'
        assert (after_held_beat["t"], after_held_beat["s"], seq) == (
            "MESSAGE_CREATE",
            2,
            2,
        )

    run(scenario)


def test_bad_payloads_are_refused_with_discord_close_codes() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        url = fake.gateway_url
        codes = [
            await close_code(session, url, identify(fake, token="wrong")),
            await close_code(session, url, identify(fake, intents="all")),
            await close_code(session, url, identify(fake), identify(fake)),
            await close_code(session, url, '{"op": 1'),
        ]

        assert codes == [4004, 4013, 4005, 4002]

    run(scenario)


def test_resume_replays_events_after_the_client_seq_then_resumed() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with session.ws_connect(fake.gateway_url) as ws:
            ready = (await handshake(fake, ws))["d"]
            await fake.dispatch("MESSAGE_CREATE", payloads.message())
            await ws.receive_str()
        await fake.dispatch("MESSAGE_DELETE", payloads.fixture("events/MESSAGE_DELETE"))
        payload = resume(fake, ready["session_id"], seq=1)
        async with session.ws_connect(ready["resume_gateway_url"]) as ws:
            await ws.receive_str()
            reaction = payloads.fixture("events/MESSAGE_REACTION_ADD")
            await fake.dispatch("MESSAGE_REACTION_ADD", reaction)
            await ws.send_json(payload)
            replayed = [await receive(ws) for _ in range(4)]
            await fake.dispatch("MESSAGE_CREATE", payloads.message())
            live = await receive(ws)

        assert [(frame["t"], frame["s"]) for frame in replayed] == [
            ("MESSAGE_CREATE", 2),
            ("MESSAGE_DELETE", 3),
            ("MESSAGE_REACTION_ADD", 4),
            ("RESUMED", 5),
        ]
        assert live["s"] == 6
        assert await fake.next_resume() == payload["d"]
        assert [connection.path for connection in fake.connections] == [
            "/gateway",
            "/gateway-resume",
        ]
        assert fake.connections[0].close_code == 1000

    run(scenario)


def test_bad_resumes_are_refused_and_identify_starts_a_new_session() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        url = fake.gateway_url
        async with session.ws_connect(url) as ws:
            first = (await handshake(fake, ws))["d"]["session_id"]
            await fake.dispatch("MESSAGE_CREATE", payloads.message())
        codes = [
            await close_code(session, url, resume(fake, first, seq=3)),
            await close_code(session, url, resume(fake, first, token="x")),
        ]
        async with session.ws_connect(url) as ws:
            ready = await handshake(fake, ws)
        async with session.ws_connect(url) as ws:
            await ws.receive_str()
            await ws.send_json(resume(fake, first))
            invalid = await ws.receive_str()

        assert codes == [4007, 4004]
        assert (ready["s"], ready["d"]["session_id"] != first) == (1, True)
        assert invalid == '{"t":null,"s":null,"op":9,"d":false}'

    run(scenario)


def test_closing_around_a_heartbeat_in_flight_logs_no_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        for yields in range(4):
            async with session.ws_connect(fake.gateway_url) as ws:
                await ws.receive_str()
                await ws.send_json({"op": 1, "d": None})
                for _ in range(yields):
                    await asyncio.sleep(0)
                await fake.close_gateway(4014)
                while (await ws.receive()).type is aiohttp.WSMsgType.TEXT:
                    pass

    run(scenario)

    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_route_windows_are_counted_per_major_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("bostie.testing.server.ROUTE_WINDOW", 60.0)

    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        responses = []
        for channel_id in ["1"] * (ROUTE_LIMIT + 1) + ["2"]:
            url = f"{fake.api_url}/channels/{channel_id}"
            async with session.get(url, headers=auth(fake)) as r:
                responses.append((r.status, r.headers.copy(), await r.json()))

        remaining = [headers["X-RateLimit-Remaining"] for _, headers, _ in responses]
        assert remaining[:ROUTE_LIMIT] == [str(n) for n in reversed(range(ROUTE_LIMIT))]
        status, headers, body = responses[ROUTE_LIMIT]
        assert (status, headers["X-RateLimit-Scope"], body["global"]) == (
            429,
            "user",
            False,
        )
        assert body["retry_after"] > 0
        assert headers["Retry-After"] == str(body["retry_after"])
        assert responses[-1][0] == 404
        assert remaining[-1] == str(ROUTE_LIMIT - 1)
        assert len({headers["X-RateLimit-Bucket"] for _, headers, _ in responses}) == 1

    run(scenario)


def test_injected_failure_stubs_a_route_the_fake_does_not_model() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        reaction = "/channels/1/messages/2/reactions/%F0%9F%91%8B/@me"
        fake.fail("PUT", "/channels/1/messages/2/reactions/", 204, None)
        results = []
        for _ in range(2):
            async with session.put(fake.api_url + reaction, headers=auth(fake)) as r:
                results.append(r.status)

        assert results == [204, 404]

    run(scenario)


def test_zstd_frames_form_one_stream_per_connection() -> None:
    zstd = pytest.importorskip("compression.zstd")

    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        frames = []
        for _ in range(2):
            async with session.ws_connect(fake.gateway_url) as ws:
                inflate = zstd.ZstdDecompressor()
                frames.append(inflate.decompress(await ws.receive_bytes()))
                await ws.send_json(identify(fake))
                frames.append(inflate.decompress(await ws.receive_bytes()))

        decoded = [msgspec.json.decode(frame) for frame in frames]
        assert [(frame["op"], frame["s"]) for frame in decoded] == [
            (10, None),
            (0, 1),
            (10, None),
            (0, 1),
        ]

    run(scenario, compress=True)


@pytest.mark.skipif(sys.version_info >= (3, 14), reason="compression.zstd exists")
def test_compress_without_zstd_fails_at_construction() -> None:
    with pytest.raises(RuntimeError, match=r"Python 3\.14"):
        FakeDiscord(compress=True)


def test_the_fake_imports_nothing_from_the_library_it_checks() -> None:
    package = Path(bostie.testing.__file__).parent
    nodes = [
        node
        for path in package.glob("*.py")
        for node in ast.parse(path.read_text()).body
    ]
    imported = {node.module for node in nodes if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in nodes
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert {name for name in imported if name and name.startswith("bostie")} == {
        "bostie.testing",
        "bostie.testing.payloads",
        "bostie.testing.server",
        "bostie.testing.voice",
    }
