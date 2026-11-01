import asyncio
import contextlib
import itertools
import secrets
import struct
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import msgspec
from aiohttp import WSCloseCode, WSMessage, WSMsgType, web
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from fakediscord.payloads import Json

AES_GCM = "aead_aes256_gcm_rtpsize"
MODES = [AES_GCM, "aead_xchacha20_poly1305_rtpsize"]
DAVE_PROTOCOL_VERSION = 1
FIRST_SSRC = 0x8000_0001
DISCOVERY = struct.Struct(">HHI64sH")
DISCOVERY_REQUEST = b"\x00\x01\x00\x46"
RTP_HEADER = struct.Struct(">2xHII")
RTP_FIRST_BYTES = b"\x80\x78"
RTP_EXTENDED_FIRST_BYTES = b"\x90\x78"
RTP_ONE_BYTE_PROFILE = 0xBEDE
SAMPLES_PER_PACKET = 960
RTP_PAYLOAD_TYPE = 120
NONCE_SIZE = 4
TAG_SIZE = 16

type Frame = tuple[WSMsgType, bytes]
type Address = tuple[str | Any, int]


@dataclass(frozen=True)
class Packet:
    """An RTP packet the fake received, opened with its session's AES-GCM key."""

    sequence: int
    timestamp: int
    ssrc: int
    nonce: int
    payload: bytes
    arrived: float


@dataclass
class VoiceSocket:
    """A voice gateway connection, with the close code the client sent if any."""

    request: web.Request
    ws: web.WebSocketResponse
    session: "VoiceSession | None" = None
    close_code: int | None = None
    closing: bool = False

    async def send(self, frame: Frame) -> None:
        """Send one frame; nothing is sent once the socket is closing."""
        if not (self.closing or self.ws.closed):
            with contextlib.suppress(ConnectionResetError):
                await self.ws.send_frame(frame[1], frame[0])

    async def close(self, code: int) -> None:
        """Send a close frame and keep reading until the client answers."""
        if not (self.closing or self.ws.closed):
            self.closing = True
            with contextlib.suppress(ConnectionResetError):
                await self.ws.send_frame(code.to_bytes(2), WSMsgType.CLOSE)


@dataclass
class VoiceSession:
    """A voice session from its Identify on.

    ``sent`` keeps every sequenced frame, so a resume can replay what was missed;
    ``address`` is the bot's UDP address from discovery.
    """

    identify: Json
    ssrc: int
    secret_key: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    mode: str | None = None
    socket: VoiceSocket | None = None
    sent: list[Frame] = field(default_factory=list)
    address: Address | None = None
    nonces: Iterator[int] = field(default_factory=itertools.count)


class _Datagrams(asyncio.DatagramProtocol):
    def __init__(self, received: Callable[[bytes, Address], None]) -> None:
        self._received = received

    def datagram_received(self, data: bytes, addr: Address) -> None:
        self._received(data, addr)


class VoiceGateway:
    """The voice gateway at ``/voice`` and its UDP media server."""

    voice_endpoint: str
    udp_port: int

    def __init__(self, heartbeat_interval: float) -> None:
        self.voice_heartbeat_interval = heartbeat_interval
        self.voice_sockets: list[VoiceSocket] = []
        self.voice_sessions: list[VoiceSession] = []
        self.voice_received: asyncio.Queue[Any] = asyncio.Queue()
        self.packets: list[Packet] = []
        self.rejected_packets: list[bytes] = []
        self._voice_identifies: asyncio.Queue[Json] = asyncio.Queue()
        self._grants: dict[str, tuple[str, str, str]] = {}
        self._current_sessions: dict[str, VoiceSession] = {}
        self._sessions_by_ssrc: dict[int, VoiceSession] = {}
        self._ssrcs = itertools.count(FIRST_SSRC)
        self._packet_arrived = asyncio.Event()
        self._udp: asyncio.DatagramTransport | None = None

    async def voice_op(self, op: int, d: Any = None) -> None:
        """Send ``op`` to the latest voice session, kept for resume replay."""
        if not self.voice_sessions:
            raise RuntimeError("no voice session yet; await next_voice_identify()")
        await self._send_sequenced(self.voice_sessions[-1], op, d)

    async def clients_connect(self, user_ids: Iterable[int]) -> None:
        """Send op 11: ``user_ids`` joined the call."""
        await self.voice_op(11, {"user_ids": [str(user_id) for user_id in user_ids]})

    async def client_disconnect(self, user_id: int) -> None:
        """Send op 13: ``user_id`` left the call."""
        await self.voice_op(13, {"user_id": str(user_id)})

    async def speaking(self, user_id: int, ssrc: int, flags: int) -> None:
        """Send op 5: ``user_id`` speaks on ``ssrc`` with ``flags``."""
        await self.voice_op(
            5, {"user_id": str(user_id), "ssrc": ssrc, "speaking": flags}
        )

    async def send_audio(
        self,
        user_id: int,
        ssrc: int,
        packets: Iterable[bytes],
        *,
        sequence: int = 0,
        timestamp: int = 0,
        extension: bytes | None = None,
    ) -> None:
        """Send ``user_id``'s Opus ``packets`` on ``ssrc`` to the bot, all at once."""
        session = self.voice_sessions[-1] if self.voice_sessions else None
        address = None if session is None else session.address
        if session is None or address is None or session.mode != AES_GCM:
            raise RuntimeError("no AES-GCM voice session with a discovered address")
        if extension is None:
            first, preamble, body = RTP_FIRST_BYTES, b"", b""
        else:
            body = extension + bytes(-len(extension) % 4)
            preamble = struct.pack(">HH", RTP_ONE_BYTE_PROFILE, len(body) // 4)
            first = RTP_EXTENDED_FIRST_BYTES
        aead = AESGCM(session.secret_key)
        for index, payload in enumerate(packets):
            fields = (
                (sequence + index) % 2**16,
                (timestamp + index * SAMPLES_PER_PACKET) % 2**32,
                ssrc,
            )
            header = first + struct.pack(">HII", *fields) + preamble
            nonce = (next(session.nonces) % 2**32).to_bytes(NONCE_SIZE, "little")
            sealed = aead.encrypt(
                nonce + bytes(12 - NONCE_SIZE), body + payload, header
            )
            self._send_datagram(header + sealed + nonce, address)

    async def next_voice_identify(self) -> Json:
        """Wait for the next voice Identify and return its ``d``."""
        return await self._voice_identifies.get()

    async def wait_for_packets(self, count: int) -> list[Packet]:
        """Wait for ``count`` RTP packets and return the first ``count``."""
        while len(self.packets) < count:
            self._packet_arrived.clear()
            await self._packet_arrived.wait()
        return self.packets[:count]

    async def _start_voice(self, http_port: int) -> None:
        self.voice_endpoint = f"ws://127.0.0.1:{http_port}/voice"
        self._udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _Datagrams(self._on_datagram), local_addr=("127.0.0.1", 0)
        )
        self.udp_port = self._udp.get_extra_info("sockname")[1]

    async def _stop_voice(self) -> None:
        for socket in self.voice_sockets:
            await socket.ws.close(code=WSCloseCode.GOING_AWAY)
        if self._udp is not None:
            self._udp.close()

    def _grant_voice(self, guild_id: str, user_id: str, session_id: str) -> str:
        token = secrets.token_hex(8)
        self._grants[guild_id] = (user_id, session_id, token)
        return token

    def _voice_session(self, guild_id: str) -> VoiceSession | None:
        return self._current_sessions.get(guild_id)

    async def _end_voice_session(self, session: VoiceSession | None, code: int) -> None:
        if session is None:
            return
        guild_id = session.identify["server_id"]
        if self._current_sessions.get(guild_id) is session:
            del self._current_sessions[guild_id]
        if session.socket is not None:
            await session.socket.close(code)

    async def _voice_gateway(self, request: web.Request) -> web.StreamResponse:
        if request.query.get("v") != "8":
            return web.Response(status=400, text="voice gateway version 8 required")
        ws = web.WebSocketResponse(autoclose=False, compress=False)
        await ws.prepare(request)
        socket = VoiceSocket(request, ws)
        self.voice_sockets.append(socket)
        await socket.send(
            _json(8, {"heartbeat_interval": self.voice_heartbeat_interval})
        )
        while (message := await ws.receive()).type in (
            WSMsgType.TEXT,
            WSMsgType.BINARY,
        ):
            if not socket.closing:
                await self._on_voice_frame(socket, message)
        if message.type is WSMsgType.CLOSE:
            socket.close_code = message.data
        await ws.close()
        if socket.session is not None and socket.session.socket is socket:
            socket.session.socket = None
        return ws

    async def _on_voice_frame(self, socket: VoiceSocket, message: WSMessage) -> None:
        if message.type is WSMsgType.BINARY:
            self.voice_received.put_nowait(message.data)
            return
        try:
            payload = msgspec.json.decode(message.data)
        except msgspec.DecodeError:
            await socket.close(4002)
            return
        self.voice_received.put_nowait(payload)
        session = socket.session
        match payload:
            case {"op": 3, "d": {"t": t}}:
                await socket.send(_json(6, {"t": t}))
            case {"op": 0, "d": dict() as identify}:
                await self._voice_identify(socket, identify)
                self._voice_identifies.put_nowait(identify)
            case {"op": 7, "d": dict() as resume}:
                await self._voice_resume(socket, resume)
            case {"op": 1 | 5 | 23 | 31} if session is None:
                await socket.close(4003)
            case {"op": 1, "d": dict() as select} if session is not None:
                await self._select_protocol(socket, session, select)
            case {"op": 5 | 23 | 31}:
                pass
            case {"op": 0 | 1 | 3 | 7}:
                await socket.close(4020)
            case _:
                await socket.close(4001)

    async def _voice_identify(self, socket: VoiceSocket, identify: Json) -> None:
        grant = (
            identify.get("user_id"),
            identify.get("session_id"),
            identify.get("token"),
        )
        if socket.session is not None:
            await socket.close(4005)
        elif self._grants.get(identify.get("server_id", "")) != grant:
            await socket.close(4004)
        elif identify.get("max_dave_protocol_version") != DAVE_PROTOCOL_VERSION:
            await socket.close(4017)
        else:
            session = VoiceSession(identify, next(self._ssrcs), socket=socket)
            socket.session = session
            self.voice_sessions.append(session)
            self._current_sessions[identify["server_id"]] = session
            self._sessions_by_ssrc[session.ssrc] = session
            ready = {
                "ssrc": session.ssrc,
                "ip": "127.0.0.1",
                "port": self.udp_port,
                "modes": MODES,
                "heartbeat_interval": 1,
            }
            await self._send_sequenced(session, 2, ready)

    async def _voice_resume(self, socket: VoiceSocket, resume: Json) -> None:
        session = self._current_sessions.get(resume.get("server_id", ""))
        seq_ack = resume.get("seq_ack", -1)
        if socket.session is not None:
            await socket.close(4005)
        elif (
            session is None
            or resume.get("session_id") != session.identify["session_id"]
            or resume.get("token") != session.identify["token"]
            or not isinstance(seq_ack, int)
            or not -1 <= seq_ack <= len(session.sent)
        ):
            await socket.close(4006)
        else:
            socket.session = session
            session.socket = None
            await socket.send(_json(9, None))
            replayed = max(seq_ack, 0)
            while replayed < len(session.sent):
                await socket.send(session.sent[replayed])
                replayed += 1
            session.socket = socket

    async def _select_protocol(
        self, socket: VoiceSocket, session: VoiceSession, select: Json
    ) -> None:
        data = select.get("data")
        if select.get("protocol") != "udp" or not isinstance(data, dict):
            await socket.close(4012)
        elif data.get("mode") not in MODES:
            await socket.close(4016)
        else:
            session.mode = data["mode"]
            description = {
                "mode": session.mode,
                "secret_key": list(session.secret_key),
                "dave_protocol_version": 0,
            }
            await self._send_sequenced(session, 4, description)

    async def _send_sequenced(self, session: VoiceSession, op: int, d: Any) -> None:
        seq = len(session.sent) + 1
        if isinstance(d, bytes):
            frame = (WSMsgType.BINARY, (seq % 65536).to_bytes(2) + bytes([op]) + d)
        else:
            body = msgspec.json.encode({"op": op, "d": d, "seq": seq})
            frame = (WSMsgType.TEXT, body)
        session.sent.append(frame)
        if session.socket is not None:
            await session.socket.send(frame)

    def _on_datagram(self, data: bytes, addr: Address) -> None:
        if len(data) == DISCOVERY.size and data.startswith(DISCOVERY_REQUEST):
            ssrc = DISCOVERY.unpack(data)[2]
            if (session := self._sessions_by_ssrc.get(ssrc)) is not None:
                session.address = addr
            answer = DISCOVERY.pack(2, 70, ssrc, addr[0].encode(), addr[1])
            self._send_datagram(answer, addr)
        elif (
            len(data) >= RTP_HEADER.size
            and data[0] >> 6 == 2
            and data[1] & 0x7F == RTP_PAYLOAD_TYPE
        ):
            packet = self._open(data)
            if packet is None:
                self.rejected_packets.append(data)
            else:
                self.packets.append(packet)
                self._packet_arrived.set()

    def _send_datagram(self, data: bytes, addr: Address) -> None:
        if self._udp is not None:
            self._udp.sendto(data, addr)

    def _open(self, data: bytes) -> Packet | None:
        sequence, timestamp, ssrc = RTP_HEADER.unpack_from(data)
        session = self._sessions_by_ssrc.get(ssrc)
        if (
            session is None
            or session.mode != AES_GCM
            or not data.startswith(RTP_FIRST_BYTES)
            or len(data) < RTP_HEADER.size + TAG_SIZE + NONCE_SIZE
        ):
            return None
        header, sealed, nonce = (
            data[: RTP_HEADER.size],
            data[RTP_HEADER.size : -NONCE_SIZE],
            data[-NONCE_SIZE:],
        )
        try:
            payload = AESGCM(session.secret_key).decrypt(
                nonce + bytes(12 - NONCE_SIZE), sealed, header
            )
        except InvalidTag:
            return None
        counter = int.from_bytes(nonce)
        return Packet(sequence, timestamp, ssrc, counter, payload, time.monotonic())


def _json(op: int, d: Any) -> Frame:
    return WSMsgType.TEXT, msgspec.json.encode({"op": op, "d": d})
