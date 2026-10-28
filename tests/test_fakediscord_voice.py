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
from fakediscord.voice import AES_GCM, MODES

type Scenario = Callable[
    [FakeDiscord, aiohttp.ClientSession], Coroutine[Any, Any, None]
]
type Socket = aiohttp.ClientWebSocketResponse

GUILD_ID = payloads.voice_channel()["guild_id"]
VOICE_ID = payloads.voice_channel()["id"]
STAGE_ID = payloads.STAGE_CHANNEL_ID
BOT_ID = payloads.voice_state()["user_id"]
MEMBER_ID = payloads.member()["user"]["id"]
VOICE_EVENTS = ["VOICE_STATE_UPDATE", "VOICE_SERVER_UPDATE"]
DISCOVERY = struct.Struct(">HHI64sH")
CLOSE = aiohttp.WSMsgType.CLOSE


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


def auth(fake: FakeDiscord) -> dict[str, str]:
    return {"Authorization": f"Bot {fake.token}"}


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


def test_a_full_channel_leaves_a_join_unanswered() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            await ws.send_json(voice_state_update(VOICE_ID))
            await ws.send_json({"op": 1, "d": 1})
            after = await receive(ws)

        assert after["op"] == 11
        assert (await fake.next_voice_state_update())["channel_id"] == VOICE_ID
        assert fake.voice_states == {}

    run(scenario, full=True)


def test_a_voice_state_update_without_every_field_is_a_decode_error() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        identify = {"token": fake.token, "intents": 1 << 7, "properties": {}}
        hello_and_identify = {"op": 2, "d": identify}
        update = voice_state_update(VOICE_ID)
        without_deaf = {"op": 4, "d": update["d"] | {"self_deaf": None}}
        numeric = {"op": 4, "d": update["d"] | {"guild_id": int(GUILD_ID)}}
        codes = [
            await close_code(session, fake.gateway_url, hello_and_identify, broken)
            for broken in (without_deaf, numeric)
        ]

        assert codes == [4002, 4002]

    run(scenario)


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


def test_ip_discovery_answers_with_the_address_the_request_came_from() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        loop = asyncio.get_running_loop()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.setblocking(False)
            udp.connect(("127.0.0.1", fake.udp_port))
            udp.send(DISCOVERY.pack(1, 70, 2**32 - 1, b"", 0))
            answer = await loop.sock_recv(udp, 128)
            local_port = udp.getsockname()[1]

        kind, length, ssrc, address, port = DISCOVERY.unpack(answer)
        assert (kind, length, ssrc, port) == (2, 70, 2**32 - 1, local_port)
        assert address.rstrip(b"\0") == b"127.0.0.1"

    run(scenario)


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


def test_resume_replays_the_frames_the_client_missed_then_stays_live() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            state, server = await join(ws)
        async with session.ws_connect(voice_url(fake)) as voice:
            await identified(voice, state, server)
            await fake.clients_connect([int(MEMBER_ID), 1])
            joined = await receive(voice)
            await voice.close(code=4000)
        await fake.speaking(int(MEMBER_ID), 42)
        await fake.voice_op(25, b"\x01\x02")
        resume = voice_resume(state, server, joined["seq"])
        async with session.ws_connect(voice_url(fake)) as voice:
            await voice.receive_str()
            await voice.send_json(resume)
            resumed = await receive(voice)
            spoke = await receive(voice)
            binary = await voice.receive_bytes()
            await fake.client_disconnect(1)
            left = await receive(voice)

        assert joined == {"op": 11, "d": {"user_ids": [MEMBER_ID, "1"]}, "seq": 2}
        assert fake.voice_sockets[0].close_code == 4000
        assert resumed == {"op": 9, "d": None}
        assert spoke == {
            "op": 5,
            "d": {"user_id": MEMBER_ID, "ssrc": 42, "speaking": 1},
            "seq": 3,
        }
        assert binary == b"\x00\x04\x19\x01\x02"
        assert left == {"op": 13, "d": {"user_id": "1"}, "seq": 5}
        assert await fake.next_voice_resume() == resume["d"]
        assert [s.session for s in fake.voice_sockets] == fake.voice_sessions * 2

    run(scenario)


def test_close_and_drop_end_the_latest_voice_socket() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with session.ws_connect(voice_url(fake)) as voice:
            await voice.receive_str()
            await fake.close_voice(4015)
            closed = await voice.receive()
        async with session.ws_connect(voice_url(fake)) as voice:
            await voice.receive_str()
            fake.drop_voice()
            dropped = await voice.receive()

        assert (closed.type, closed.data) == (CLOSE, 4015)
        assert dropped.type is not CLOSE

    run(scenario)


def test_leaving_drops_the_channel_and_closes_the_voice_session_with_4014() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            state, server = await join(ws)
            async with session.ws_connect(voice_url(fake)) as voice:
                await identified(voice, state, server)
                await ws.send_json(voice_state_update(None))
                [(name, left)] = await dispatches(ws, 1)
                closed = await voice.receive()
            await ws.send_json(voice_state_update(None))
            await ws.send_json({"op": 1, "d": 1})
            after_second_leave = await receive(ws)
        resumed = await close_code(
            session, voice_url(fake), voice_resume(state, server, 1)
        )

        assert (name, left["channel_id"], left["user_id"]) == (
            "VOICE_STATE_UPDATE",
            None,
            BOT_ID,
        )
        assert (closed.type, closed.data) == (CLOSE, 4014)
        assert after_second_leave["op"] == 11
        assert resumed == 4006
        assert fake.voice_states == {}

    run(scenario)


@pytest.mark.parametrize(
    ("order", "names"),
    [("state_first", VOICE_EVENTS), ("server_first", VOICE_EVENTS[::-1])],
)
def test_moving_the_bot_announces_the_channel_and_retires_the_old_session(
    order: Order, names: list[str]
) -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            state, server = await join(ws)
            async with session.ws_connect(voice_url(fake)) as voice:
                first = await identified(voice, state, server)
                await fake.move_bot(int(STAGE_ID), order=order)
                moved = await dispatches(ws, 2)
                closed = await voice.receive()
        new_state = dict(moved)["VOICE_STATE_UPDATE"]
        new_server = dict(moved)["VOICE_SERVER_UPDATE"]
        stale = await close_code(
            session, voice_url(fake), voice_identify(state, server)
        )
        async with session.ws_connect(voice_url(fake)) as voice:
            second = await identified(voice, new_state, new_server)

        assert [name for name, _ in moved] == names
        assert (new_state["channel_id"], new_state["self_deaf"]) == (STAGE_ID, True)
        assert new_server["token"] != server["token"]
        assert (closed.type, closed.data) == (CLOSE, 4014)
        assert stale == 4004
        assert second["d"]["ssrc"] != first["d"]["ssrc"]

    run(scenario)


def test_kicking_the_bot_drops_its_channel_and_closes_voice_with_4014() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            state, server = await join(ws)
            async with session.ws_connect(voice_url(fake)) as voice:
                await identified(voice, state, server)
                await fake.kick_bot()
                [(name, kicked)] = await dispatches(ws, 1)
                closed = await voice.receive()
        with pytest.raises(RuntimeError, match="no voice channel"):
            await fake.kick_bot()

        assert (name, kicked["channel_id"]) == ("VOICE_STATE_UPDATE", None)
        assert (closed.type, closed.data) == (CLOSE, 4014)

    run(scenario)


def test_server_update_brings_a_new_token_with_or_without_an_endpoint() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with gateway(fake, session) as ws:
            state, server = await join(ws)
            await fake.server_update(None)
            await fake.server_update(fake.voice_endpoint)
            [(_, gone), (_, back)] = await dispatches(ws, 2)
        async with session.ws_connect(voice_url(fake)) as voice:
            ready = await identified(voice, state, back)

        assert gone == {"token": gone["token"], "guild_id": GUILD_ID, "endpoint": None}
        assert back["endpoint"] == fake.voice_endpoint
        assert len({server["token"], gone["token"], back["token"]}) == 3
        assert ready["op"] == 2

    run(scenario)


def test_voice_state_routes_serve_scripted_states_and_edit_the_bots_own() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        member_state = payloads.voice_state(user_id=MEMBER_ID, channel_id=VOICE_ID)
        fake.voice_states[(GUILD_ID, MEMBER_ID)] = member_state
        fake.suppress = True
        states = f"{fake.api_url}/guilds/{GUILD_ID}/voice-states"
        before = []
        for method, user, body in [
            ("GET", MEMBER_ID, None),
            ("GET", "1", None),
            ("PATCH", "@me", {"suppress": False}),
        ]:
            async with session.request(
                method, f"{states}/{user}", json=body, headers=auth(fake)
            ) as r:
                before.append((r.status, await r.json()))
        edit = {
            "channel_id": STAGE_ID,
            "suppress": False,
            "request_to_speak_timestamp": None,
        }
        async with gateway(fake, session) as ws:
            state, _ = await join(ws, STAGE_ID)
            async with session.patch(
                f"{states}/@me", json=edit, headers=auth(fake)
            ) as r:
                edited = r.status
            [(_, update)] = await dispatches(ws, 1)
            async with session.get(f"{states}/@me", headers=auth(fake)) as r:
                mine = await r.json()
        channels = []
        for channel_id in (VOICE_ID, STAGE_ID):
            url = f"{fake.api_url}/channels/{channel_id}"
            async with session.get(url, headers=auth(fake)) as r:
                channels.append(await r.json())

        unknown = {"message": "Unknown Voice State", "code": 10065}
        assert before == [(200, member_state), (404, unknown), (404, unknown)]
        assert (state["suppress"], edited) == (True, 204)
        assert update == state | {"suppress": False}
        assert mine == update
        assert [r.json for r in fake.requests if r.method == "PATCH"] == [
            {"suppress": False},
            edit,
        ]
        assert [(c["guild_id"], c["type"], c["bitrate"]) for c in channels] == [
            (GUILD_ID, 2, 64000),
            (GUILD_ID, 13, 64000),
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
