import asyncio
import contextlib
import os
import socket
import struct
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

import aiohttp
import msgspec
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fakediscord import FakeDiscord, Order, payloads
from fakediscord.payloads import Json
from fakediscord.voice import AES_GCM, DISCOVERY, MODES

type Scenario = Callable[
    [FakeDiscord, aiohttp.ClientSession], Coroutine[Any, Any, None]
]
type Socket = aiohttp.ClientWebSocketResponse

GUILD_ID = payloads.voice_channel()["guild_id"]
VOICE_ID = payloads.voice_channel()["id"]
BOT_ID = payloads.voice_state()["user_id"]
VOICE_EVENTS = ["VOICE_STATE_UPDATE", "VOICE_SERVER_UPDATE"]


def run(scenario: Scenario, **options: Any) -> None:
    async def main() -> None:
        async with (
            FakeDiscord(voice=True, **options) as fake,
            aiohttp.ClientSession() as session,
        ):
            async with asyncio.timeout(5):
                await scenario(fake, session)

    asyncio.run(main())


async def receive(ws: Socket) -> Any:
    return msgspec.json.decode(await ws.receive_str())


@contextlib.asynccontextmanager
async def gateway(
    fake: FakeDiscord, session: aiohttp.ClientSession
) -> AsyncIterator[Socket]:
    async with session.ws_connect(fake.gateway_url) as ws:
        await ws.receive_str()
        identify = {"token": fake.token, "intents": 1 << 7, "properties": {}}
        await ws.send_json({"op": 2, "d": identify})
        await ws.receive_str()
        yield ws


def voice_state_update(channel_id: str | None, **changes: Any) -> Json:
    d = {
        "guild_id": GUILD_ID,
        "channel_id": channel_id,
        "self_mute": False,
        "self_deaf": True,
    }
    return {"op": 4, "d": d | changes}


async def dispatches(ws: Socket, count: int) -> list[tuple[str, Json]]:
    frames = [await receive(ws) for _ in range(count)]
    return [(frame["t"], frame["d"]) for frame in frames]


async def join(ws: Socket, channel_id: str = VOICE_ID) -> tuple[Json, Json]:
    await ws.send_json(voice_state_update(channel_id))
    events = dict(await dispatches(ws, 2))
    return events["VOICE_STATE_UPDATE"], events["VOICE_SERVER_UPDATE"]


def voice_identify(state: Json, server: Json, **changes: Any) -> Json:
    d = {
        "server_id": server["guild_id"],
        "user_id": state["user_id"],
        "session_id": state["session_id"],
        "token": server["token"],
        "max_dave_protocol_version": 1,
    }
    return {"op": 0, "d": d | changes}


def voice_resume(state: Json, server: Json, seq_ack: int) -> Json:
    d = {
        "server_id": server["guild_id"],
        "session_id": state["session_id"],
        "token": server["token"],
        "seq_ack": seq_ack,
    }
    return {"op": 7, "d": d}


def select_protocol(mode: str = AES_GCM, protocol: str = "udp") -> Json:
    data = {"address": "127.0.0.1", "port": 50000, "mode": mode}
    return {"op": 1, "d": {"protocol": protocol, "data": data}}


def voice_url(fake: FakeDiscord) -> str:
    return fake.voice_endpoint + "?v=8"


async def identified(ws: Socket, state: Json, server: Json) -> Json:
    await ws.receive_str()
    await ws.send_json(voice_identify(state, server))
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


def seal(
    key: bytes,
    header: tuple[int, int, int],
    nonce: int,
    payload: bytes,
    first: bytes = b"\x80\x78",
) -> bytes:
    rtp = first + struct.pack(">HII", *header)
    counter = nonce.to_bytes(4)
    return rtp + AESGCM(key).encrypt(counter + bytes(8), payload, rtp) + counter


@pytest.mark.parametrize(
    ("order", "names"),
    [("state_first", VOICE_EVENTS), ("server_first", VOICE_EVENTS[::-1])],
)
def test_joining_answers_with_the_bot_voice_state_and_a_fresh_voice_server(
    order: Order, names: list[str]
) -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        fake.mute = True
        fake.suppress = True
        async with session.ws_connect(fake.gateway_url) as ws:
            await ws.receive_str()
            identify = {"token": fake.token, "intents": 1 << 7, "properties": {}}
            await ws.send_json({"op": 2, "d": identify})
            session_id = (await receive(ws))["d"]["session_id"]
            await ws.send_json(voice_state_update(VOICE_ID))
            first = await dispatches(ws, 2)
            await ws.send_json(voice_state_update(VOICE_ID, self_mute=True))
            again = dict(await dispatches(ws, 2))

        state = dict(first)["VOICE_STATE_UPDATE"]
        server = dict(first)["VOICE_SERVER_UPDATE"]
        assert [name for name, _ in first] == names
        assert (state["guild_id"], state["channel_id"], state["user_id"]) == (
            GUILD_ID,
            VOICE_ID,
            BOT_ID,
        )
        assert state["session_id"] == session_id
        assert (
            state["self_mute"],
            state["self_deaf"],
            state["mute"],
            state["suppress"],
        ) == (False, True, True, True)
        assert server == {
            "token": server["token"],
            "guild_id": GUILD_ID,
            "endpoint": fake.gateway_url.replace("/gateway", "/voice"),
        }
        assert again["VOICE_SERVER_UPDATE"]["token"] != server["token"]
        assert again["VOICE_STATE_UPDATE"]["self_mute"] is True
        assert fake.voice_states == {(GUILD_ID, BOT_ID): again["VOICE_STATE_UPDATE"]}
        assert [await fake.next_voice_state_update() for _ in range(2)] == [
            voice_state_update(VOICE_ID)["d"],
            voice_state_update(VOICE_ID, self_mute=True)["d"],
        ]

    run(scenario, voice_order=order)


def test_voice_handshake_runs_from_hello_to_session_description() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            state, server = await join(ws)
        async with session.ws_connect(voice_url(fake)) as voice:
            hello = await receive(voice)
            await voice.send_json(voice_identify(state, server))
            ready = await receive(voice)
            await voice.send_json({"op": 3, "d": {"t": 1501184119561, "seq_ack": 1}})
            ack = await receive(voice)
            await voice.send_json(select_protocol())
            description = await receive(voice)

        ssrc = ready["d"]["ssrc"]
        assert hello == {"op": 8, "d": {"heartbeat_interval": 13750.5}}
        assert ready == {
            "op": 2,
            "d": {
                "ssrc": ssrc,
                "ip": "127.0.0.1",
                "port": fake.udp_port,
                "modes": MODES,
                "heartbeat_interval": 1,
            },
            "seq": 1,
        }
        assert 2**31 < ssrc < 2**32
        assert ack == {"op": 6, "d": {"t": 1501184119561}}
        [voice_session] = fake.voice_sessions
        assert description == {
            "op": 4,
            "d": {
                "mode": AES_GCM,
                "secret_key": list(voice_session.secret_key),
                "dave_protocol_version": 0,
            },
            "seq": 2,
        }
        assert len(voice_session.secret_key) == 32
        assert voice_session.mode == AES_GCM
        assert fake.voice_sockets[0].session is voice_session
        assert await fake.next_voice_identify() == voice_identify(state, server)["d"]
        received = [fake.voice_received.get_nowait() for _ in range(3)]
        assert [payload["op"] for payload in received] == [0, 3, 1]

    run(scenario, voice_heartbeat_interval=13750.5)


def test_voice_gateway_refuses_bad_payloads_with_voice_close_codes() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            state, server = await join(ws)
        url = voice_url(fake)
        identify = voice_identify(state, server)
        codes = [
            await close_code(session, url, '{"op": 0'),
            await close_code(session, url, {"op": 99, "d": None}),
            await close_code(session, url, select_protocol()),
            await close_code(session, url, {"op": 3, "d": 1}),
            await close_code(session, url, voice_identify(state, server, token="x")),
            await close_code(
                session, url, voice_identify(state, server, server_id=int(GUILD_ID))
            ),
            await close_code(
                session, url, voice_identify(state, server, max_dave_protocol_version=0)
            ),
            await close_code(session, url, identify, identify),
            await close_code(session, url, identify, select_protocol(protocol="tcp")),
            await close_code(
                session, url, identify, select_protocol(mode="xsalsa20_poly1305")
            ),
            await close_code(
                session, url, voice_resume(state, server | {"token": "x"}, -1)
            ),
        ]
        with pytest.raises(aiohttp.WSServerHandshakeError) as unversioned:
            await session.ws_connect(fake.voice_endpoint)

        assert codes == [
            4002,
            4001,
            4003,
            4020,
            4004,
            4004,
            4017,
            4005,
            4012,
            4016,
            4006,
        ]
        assert unversioned.value.status == 400

    run(scenario)


def test_rtp_packets_open_with_the_session_key_and_bad_ones_are_kept_aside() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            state, server = await join(ws)
        async with session.ws_connect(voice_url(fake)) as voice:
            ssrc = (await identified(voice, state, server))["d"]["ssrc"]
            await voice.send_json(select_protocol())
            key = bytes((await receive(voice))["d"]["secret_key"])
            tampered = bytearray(seal(key, (1, 960, ssrc), 0, b"opus"))
            tampered[13] ^= 1
            rejected = [
                bytes(tampered),
                seal(key, (2, 1920, ssrc), 1, b"opus", first=b"\x90\x78"),
                seal(key, (3, 2880, ssrc), 2, b"opus", first=b"\x80\xf8"),
                seal(key, (4, 3840, ssrc + 1), 3, b"opus"),
                seal(bytes(32), (5, 4800, ssrc), 4, b"opus"),
            ]
            accepted = [
                seal(key, (65535, 2**32 - 960, ssrc), 2**32 - 1, b"\xf8\xff\xfe"),
                seal(key, (0, 0, ssrc), 0, b"opus"),
            ]
            rtcp = b"\x80\xc8\x00\x06" + bytes(24)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.connect(("127.0.0.1", fake.udp_port))
                for datagram in [*rejected, rtcp, *accepted]:
                    udp.send(datagram)
                packets = await fake.wait_for_packets(2)

        assert [
            (p.sequence, p.timestamp, p.ssrc, p.nonce, p.payload) for p in packets
        ] == [
            (65535, 2**32 - 960, ssrc, 2**32 - 1, b"\xf8\xff\xfe"),
            (0, 0, ssrc, 0, b"opus"),
        ]
        assert packets[0].arrived <= packets[1].arrived
        assert fake.rejected_packets == rejected

    run(scenario)


def test_audio_sent_to_the_bot_opens_with_its_key_in_consecutive_packets() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            state, server = await join(ws)
        async with session.ws_connect(voice_url(fake)) as voice:
            ssrc = (await identified(voice, state, server))["d"]["ssrc"]
            await voice.send_json(select_protocol())
            key = bytes((await receive(voice))["d"]["secret_key"])
            loop = asyncio.get_running_loop()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.setblocking(False)
                udp.connect(("127.0.0.1", fake.udp_port))
                udp.send(DISCOVERY.pack(1, 70, ssrc, b"", 0))
                await loop.sock_recv(udp, DISCOVERY.size)
                await fake.send_audio(
                    4,
                    4321,
                    [b"first", b"second"],
                    sequence=65535,
                    timestamp=2**32 - 960,
                    extension=b"\x10\x7f",
                )
                await fake.send_audio(4, 4321, [b"plain"])
                datagrams = [await loop.sock_recv(udp, 2048) for _ in range(3)]

        opened = [
            (
                datagram[:size],
                int.from_bytes(datagram[-4:], "little"),
                AESGCM(key).decrypt(
                    datagram[-4:] + bytes(8), datagram[size:-4], datagram[:size]
                ),
            )
            for datagram, size in zip(datagrams, [16, 16, 12], strict=True)
        ]
        preamble = b"\xbe\xde\x00\x01"
        assert opened == [
            (
                b"\x90\x78" + struct.pack(">HII", 65535, 2**32 - 960, 4321) + preamble,
                0,
                b"\x10\x7f\x00\x00first",
            ),
            (
                b"\x90\x78" + struct.pack(">HII", 0, 0, 4321) + preamble,
                1,
                b"\x10\x7f\x00\x00second",
            ),
            (b"\x80\x78" + struct.pack(">HII", 0, 0, 4321), 2, b"plain"),
        ]

    run(scenario)


def test_the_fake_never_imports_bostie() -> None:
    code = "import sys; sys.modules['bostie'] = None; import fakediscord"
    tests = str(Path(__file__).parent)
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=os.environ | {"PYTHONPATH": tests},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
