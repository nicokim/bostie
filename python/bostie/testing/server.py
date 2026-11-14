import asyncio
import contextlib
import math
import socket
import sys
import time
import uuid
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Literal, Self

import msgspec
from aiohttp import BodyPartReader, WSCloseCode, WSMsgType, hdrs, web
from aiohttp.typedefs import Handler

from bostie.testing import payloads
from bostie.testing.payloads import Json, is_object
from bostie.testing.voice import VoiceGateway

API = "/api/v10"
ROUTE_LIMIT = 5
ROUTE_WINDOW = 1.0
MAJOR_PARAMETERS = ("channel_id", "guild_id", "interaction_token")
MESSAGE_FIELDS = ("content", "embeds", "components", "flags", "tts")
GATEWAY_ONLY_MESSAGE_FIELDS = ("guild_id", "member", "nonce", "channel_type")
CLEARED: Json = {"content": "", "embeds": []}
EPHEMERAL = 1 << 6
LAUNCH_ACTIVITY = 12
LOADING = 1 << 7
LOCALIZATIONS = ("name_localizations", "description_localizations")
TRACE = '["gateway-fake",{"micros":0,"calls":[]}]'
FAKE_TOKEN = "fake-token"
DEFERRED_WITH_SOURCE = 5

type Respond = Callable[[dict[str, str]], web.Response]
type Order = Literal["state_first", "server_first"]


@dataclass(frozen=True)
class Part:
    """One part of a multipart request body."""

    name: str | None
    filename: str | None
    content_type: str | None
    data: bytes


@dataclass(frozen=True)
class Recorded:
    """A REST request, with `path` relative to `api_url`.

    `json` holds the JSON body, the `payload_json` part of a multipart body, or
    the fields of a form-urlencoded body.
    """

    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    json: Any
    parts: list[Part]


@dataclass
class _Session:
    id: str
    buffer: list[bytes] = field(default_factory=list[bytes])


@dataclass
class Connection:
    """A gateway connection, with the close code the client sent if it closed first."""

    request: web.Request
    ws: web.WebSocketResponse
    compress: Callable[[bytes], bytes] | None
    live: bool = False
    close_code: int | None = None
    shard: int = 0
    received: list[Any] = field(default_factory=list[Any])

    @property
    def path(self) -> str:
        """`/gateway` or `/gateway-resume`."""
        return self.request.path

    async def send(self, frame: bytes) -> None:
        """Send one gateway payload, compressed in `compress=True` mode.

        A payload sent to a closing connection is dropped.
        """
        with contextlib.suppress(ConnectionResetError):
            if self.compress is None:
                await self.ws.send_frame(frame, WSMsgType.TEXT)
            else:
                await self.ws.send_bytes(self.compress(frame))


@dataclass
class _Fault:
    method: str
    route_prefix: str
    respond: Respond


@dataclass
class _Hold:
    method: str
    route_prefix: str
    release: asyncio.Event


RECORD = web.RequestKey("record", Recorded)


class FakeDiscord(VoiceGateway):
    """A local Discord gateway and REST API for tests, served inside `async with`.

    `voice=True` adds voice: op 4 answers, the voice gateway, UDP and voice routes.
    `mute`, `deaf` and `suppress` go into every voice state the fake sends for
    the bot. `ready_guilds` are the guild ids READY lists, each on its own shard.
    """

    def __init__(
        self,
        *,
        token: str = FAKE_TOKEN,
        heartbeat_interval: int = 41_250,
        compress: bool = False,
        voice: bool = False,
        voice_heartbeat_interval: float = 13_750.0,
        voice_order: Order = "state_first",
    ) -> None:
        self._zstd_stream = _zstd_streams() if compress else None
        self._listener = socket.create_server(("127.0.0.1", 0))
        port = self._listener.getsockname()[1]
        super().__init__(voice_heartbeat_interval, port)
        self.api_url = f"http://127.0.0.1:{port}{API}"
        self.gateway_url = f"ws://127.0.0.1:{port}/gateway"
        self._resume_url = f"ws://127.0.0.1:{port}/gateway-resume"
        self.token = token
        self.application_id = int(payloads.fixture("rest/application_me")["id"])
        self.bot_user: Json = payloads.fixture("events/READY")["user"]
        guild = payloads.fixture("rest/guild")
        channel = payloads.channel()
        member = payloads.member()
        self.guilds: dict[str, Json] = {guild["id"]: guild}
        self.channels: dict[str, Json] = {channel["id"]: channel}
        self.ready_guilds: list[int] = []
        self.users: dict[str, Json] = {
            user["id"]: user for user in (payloads.user(), member["user"])
        }
        self.members: dict[tuple[str, str], Json] = {
            (guild["id"], member["user"]["id"]): member
        }
        self.messages: dict[str, dict[str, Json]] = {}
        self.commands: list[Json] = []
        self.guild_commands: dict[str, list[Json]] = {}
        self.put_count = 0
        self.requests: list[Recorded] = []
        self.gateway_received: asyncio.Queue[Any] = asyncio.Queue()
        self.connections: list[Connection] = []
        self._heartbeat_interval = heartbeat_interval
        self._hold_acks = False
        self._faults: list[_Fault] = []
        self._holds: list[_Hold] = []
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}
        self._sessions: dict[int, _Session] = {}
        self._identifies: asyncio.Queue[Json] = asyncio.Queue()
        self._identify_holds: dict[int, asyncio.Event] = {}
        self._hello_holds: list[asyncio.Event] = []
        self._resumes: asyncio.Queue[Json] = asyncio.Queue()
        self._interactions: dict[str, Json] = {}
        self._responses: dict[str, dict[str, Json]] = {}
        self._callback_waiters: dict[str, asyncio.Future[Recorded]] = {}
        self.voice_order: Order = voice_order
        self.mute = False
        self.deaf = False
        self.suppress = False
        self.voice_states: dict[tuple[str, str], Json] = {}
        self._voice = voice
        self._voice_state_updates: asyncio.Queue[Any] = asyncio.Queue()
        if voice:
            for voice_channel in (payloads.voice_channel(), payloads.stage_channel()):
                self.channels[voice_channel["id"]] = voice_channel
        self._runner = web.AppRunner(self._app(), access_log=None)

    def _app(self) -> web.Application:
        rest_routes: list[tuple[str, str, Handler]] = [
            ("GET", "/gateway/bot", self._gateway_bot),
            ("GET", "/applications/@me", self._application),
            ("GET", "/applications/{application_id}/commands", self._commands),
            ("PUT", "/applications/{application_id}/commands", self._commands),
            *(
                (
                    method,
                    "/applications/{application_id}/guilds/{guild_id}/commands",
                    self._commands,
                )
                for method in ("GET", "PUT")
            ),
            (
                "POST",
                "/interactions/{interaction_id}/{interaction_token}/callback",
                self._callback,
            ),
            ("POST", "/webhooks/{application_id}/{interaction_token}", self._followup),
            *(
                (
                    method,
                    "/webhooks/{application_id}/{interaction_token}/messages/{message_id}",
                    self._webhook_message,
                )
                for method in ("GET", "PATCH", "DELETE")
            ),
            ("POST", "/channels/{channel_id}/messages", self._create_message),
            *(
                (
                    method,
                    "/channels/{channel_id}/messages/{message_id}",
                    self._channel_message,
                )
                for method in ("GET", "PATCH", "DELETE")
            ),
            ("GET", "/channels/{channel_id}", self._channel),
            ("GET", "/guilds/{guild_id}", self._guild),
            ("GET", "/guilds/{guild_id}/members/{user_id}", self._member),
            ("GET", "/users/{user_id}", self._user),
        ]
        routes = [
            web.get("/gateway", self._gateway),
            web.get("/gateway-resume", self._gateway),
        ]
        if self._voice:
            rest_routes += [
                ("GET", "/guilds/{guild_id}/voice-states/{user_id}", self._voice_state),
                (
                    "PATCH",
                    "/guilds/{guild_id}/voice-states/@me",
                    self._edit_voice_state,
                ),
            ]
            routes.append(web.get("/voice", self._voice_gateway))
        app = web.Application(middlewares=[self._rest])
        app.add_routes(
            [
                *routes,
                *(
                    web.route(method, API + path, handler)
                    for method, path, handler in rest_routes
                ),
            ]
        )
        return app

    async def __aenter__(self) -> Self:
        await self._runner.setup()
        await web.SockSite(self._runner, self._listener).start()
        if self._voice:
            await self._start_voice()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for held in [*self._identify_holds.values(), *self._hello_holds]:
            held.set()
        for connection in self.connections:
            await connection.ws.close(code=WSCloseCode.GOING_AWAY)
        await self._stop_voice()
        await self._runner.cleanup()

    async def dispatch(self, name: str, d: Any, *, shard: int = 0) -> int:
        """Send a dispatch event on a shard, kept for RESUME replay; return its seq."""
        session = self._sessions.get(shard)
        if session is None:
            raise RuntimeError(f"shard {shard} has no session; await next_identify()")
        if name == "INTERACTION_CREATE" and "token" in d:
            self._interactions[d["token"]] = d
        seq = self._append(session, name, d)
        live = [c for c in self.connections if c.shard == shard and c.live]
        if live:
            await live[-1].send(session.buffer[seq - 1])
        return seq

    async def send_op(self, op: int, d: Any = None) -> None:
        """Send a non-dispatch opcode, such as 7 (reconnect) or 9 (invalid session)."""
        await self._latest().send(_frame(None, None, op, d))

    async def close_gateway(self, code: int) -> None:
        """Close the latest gateway connection with `code`."""
        await self._latest().ws.close(code=code)

    def drop_gateway(self) -> None:
        """Abort the latest gateway connection's TCP stream without a close frame."""
        transport = self._latest().request.transport
        if transport is not None:
            transport.abort()

    def hold_acks(self) -> None:
        """Leave heartbeats unanswered from now on, like a zombie link."""
        self._hold_acks = True

    async def next_voice_state_update(self) -> Any:
        """Wait for the next op 4 the bot sent and return its `d`."""
        return await self._voice_state_updates.get()

    async def move_bot(self, channel_id: int) -> None:
        """Move the bot to `channel_id` as a moderator would.

        The voice socket of the old channel then closes with 4014.
        """
        state = self._bot_voice_state()
        await self._join_voice(
            state["guild_id"],
            str(channel_id),
            self_mute=state["self_mute"],
            self_deaf=state["self_deaf"],
            order="state_first",
        )

    async def kick_bot(self) -> None:
        """Disconnect the bot from voice: a voice state without channel, then 4014."""
        state = self._bot_voice_state()
        await self._leave_voice(
            state["guild_id"],
            self_mute=state["self_mute"],
            self_deaf=state["self_deaf"],
        )

    async def next_identify(self) -> Json:
        """Wait for the next IDENTIFY and return its `d`."""
        return await self._identifies.get()

    def hold_identify(self, shard: int) -> asyncio.Event:
        """Leave `shard`'s IDENTIFY unanswered until the returned event is set."""
        release = self._identify_holds[shard] = asyncio.Event()
        return release

    def hold_hello(self) -> asyncio.Event:
        """Leave the next gateway connection without HELLO until the event is set."""
        release = asyncio.Event()
        self._hello_holds.append(release)
        return release

    async def next_resume(self) -> Json:
        """Wait for the next RESUME and return its `d`."""
        return await self._resumes.get()

    async def interaction(self, type: int, data: Json, **fields: Any) -> Recorded:
        """Dispatch `payloads.interaction(...)` and return its first callback."""
        payload = payloads.interaction(type, data, **fields)
        callback = self.expect_callback(payload)
        await self.dispatch("INTERACTION_CREATE", payload)
        return await callback

    def expect_callback(self, interaction: Json) -> asyncio.Future[Recorded]:
        """Accept callbacks for `interaction`; the future gets the first one."""
        self._interactions[interaction["token"]] = interaction
        callback: asyncio.Future[Recorded] = asyncio.get_running_loop().create_future()
        self._callback_waiters[interaction["id"]] = callback
        return callback

    def rate_limit(
        self,
        method: str,
        route_prefix: str,
        *,
        scope: Literal["user", "shared", "global"] = "user",
        retry_after: float = 0.1,
    ) -> None:
        """Answer the next matching request with a 429 of `scope`."""
        self._faults.append(
            _Fault(
                method,
                route_prefix,
                lambda window: _rate_limited(window, scope, retry_after),
            )
        )

    def fail(self, method: str, route_prefix: str, status: int, json: Any) -> None:
        """Answer the next matching request with `status` and `json`.

        This also stubs routes the fake does not model, which otherwise get a 404.
        """
        self._faults.append(
            _Fault(method, route_prefix, lambda window: _json(json, status, window))
        )

    def answer(
        self, method: str, route_prefix: str, body: bytes, content_type: str
    ) -> None:
        """Answer the next matching request with a 200 that is not JSON."""
        self._faults.append(
            _Fault(
                method,
                route_prefix,
                lambda window: web.Response(
                    body=body, content_type=content_type, headers=window
                ),
            )
        )

    def hold(self, method: str, route_prefix: str) -> asyncio.Event:
        """Keep the next matching request waiting until the returned event is set."""
        release = asyncio.Event()
        self._holds.append(_Hold(method, route_prefix, release))
        return release

    def _latest(self) -> Connection:
        if not self.connections:
            raise RuntimeError("no gateway connection yet")
        return self.connections[-1]

    def _append(self, session: _Session, name: str, d: Any) -> int:
        session.buffer.append(_frame(name, len(session.buffer) + 1, 0, d))
        return len(session.buffer)

    async def _catch_up(
        self, connection: Connection, session: _Session, seq: int
    ) -> None:
        while seq < len(session.buffer):
            await connection.send(session.buffer[seq])
            seq += 1
        connection.live = True

    async def _gateway(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(autoclose=False, compress=False)
        await ws.prepare(request)
        compress = None if self._zstd_stream is None else self._zstd_stream()
        connection = Connection(request, ws, compress)
        self.connections.append(connection)
        if self._hello_holds:
            await self._hello_holds.pop(0).wait()
        hello = {"heartbeat_interval": self._heartbeat_interval, "_trace": [TRACE]}
        await connection.send(_frame(None, None, 10, hello))
        while (message := await ws.receive()).type in (
            WSMsgType.TEXT,
            WSMsgType.BINARY,
        ):
            try:
                payload = msgspec.json.decode(message.data)
            except msgspec.DecodeError:
                await ws.close(code=4002, message=b"Decode error")
            else:
                await self._receive(connection, payload)
        if message.type is WSMsgType.CLOSE:
            connection.close_code = message.data
        await ws.close()
        return ws

    async def _receive(self, connection: Connection, payload: Any) -> None:
        self.gateway_received.put_nowait(payload)
        connection.received.append(payload)
        match payload:
            case {"op": 1} if not self._hold_acks:
                await connection.send(_frame(None, None, 11, None))
            case {"op": 2, "d": identify} if is_object(identify):
                await self._identify(connection, identify)
                self._identifies.put_nowait(identify)
            case {"op": 6, "d": resume} if is_object(resume):
                await self._resume(connection, resume)
                self._resumes.put_nowait(resume)
            case {"op": 4, "d": update} if self._voice:
                await self._update_voice_state(connection, update)
            case _:
                pass

    async def _identify(self, connection: Connection, identify: Json) -> None:
        if connection.live:
            await connection.ws.close(code=4005, message=b"Already authenticated")
        elif identify.get("token") != self.token:
            await connection.ws.close(code=4004, message=b"Authentication failed")
        elif not isinstance(identify.get("intents"), int):
            await connection.ws.close(code=4013, message=b"Invalid intent(s)")
        else:
            shard = identify.get("shard", [0, 1])
            if (held := self._identify_holds.get(shard[0])) is not None:
                await held.wait()
            connection.shard = shard[0]
            session = self._sessions[shard[0]] = _Session(uuid.uuid4().hex)
            ready = payloads.fixture("events/READY") | {
                "user": self.bot_user,
                "session_id": session.id,
                "resume_gateway_url": self._resume_url,
                "shard": shard,
                "guilds": [
                    {"id": str(guild_id), "unavailable": True}
                    for guild_id in self.ready_guilds
                    if (guild_id >> 22) % shard[1] == shard[0]
                ],
            }
            self._append(session, "READY", ready)
            await self._catch_up(connection, session, 0)

    async def _resume(self, connection: Connection, resume: Json) -> None:
        seq = resume.get("seq")
        shard, session = next(
            (
                (shard, session)
                for shard, session in self._sessions.items()
                if session.id == resume.get("session_id")
            ),
            (0, None),
        )
        if resume.get("token") != self.token:
            await connection.ws.close(code=4004, message=b"Authentication failed")
        elif session is None:
            await connection.send(_frame(None, None, 9, False))
        elif not isinstance(seq, int) or not 0 <= seq <= len(session.buffer):
            await connection.ws.close(code=4007, message=b"Invalid seq")
        else:
            connection.shard = shard
            self._append(session, "RESUMED", {"_trace": [TRACE]})
            await self._catch_up(connection, session, seq)

    async def _update_voice_state(self, connection: Connection, update: Any) -> None:
        self._voice_state_updates.put_nowait(update)
        match update:
            case {
                "guild_id": str() as guild_id,
                "channel_id": str() | None as channel_id,
                "self_mute": bool() as self_mute,
                "self_deaf": bool() as self_deaf,
            }:
                if channel_id is None:
                    await self._leave_voice(
                        guild_id, self_mute=self_mute, self_deaf=self_deaf
                    )
                else:
                    await self._join_voice(
                        guild_id,
                        channel_id,
                        self_mute=self_mute,
                        self_deaf=self_deaf,
                        order=self.voice_order,
                    )
            case _:
                await connection.ws.close(code=4002, message=b"Decode error")

    async def _join_voice(
        self,
        guild_id: str,
        channel_id: str,
        *,
        self_mute: bool,
        self_deaf: bool,
        order: Order,
    ) -> None:
        key = (guild_id, self.bot_user["id"])
        previous = self.voice_states.get(key)
        session = self._voice_session(guild_id)
        state = self._bot_voice(
            guild_id, channel_id, self_mute=self_mute, self_deaf=self_deaf
        )
        self.voice_states[key] = state
        token = self._grant_voice(guild_id, state["user_id"], state["session_id"])
        server = {"token": token, "guild_id": guild_id, "endpoint": self.voice_endpoint}
        events = [("VOICE_STATE_UPDATE", state), ("VOICE_SERVER_UPDATE", server)]
        for name, d in events if order == "state_first" else events[::-1]:
            await self.dispatch(name, d)
        if previous is not None and previous["channel_id"] != channel_id:
            await self._end_voice_session(session, 4014)

    async def _leave_voice(
        self, guild_id: str, *, self_mute: bool, self_deaf: bool
    ) -> None:
        if self.voice_states.pop((guild_id, self.bot_user["id"]), None) is None:
            return
        session = self._voice_session(guild_id)
        state = self._bot_voice(
            guild_id, None, self_mute=self_mute, self_deaf=self_deaf
        )
        await self.dispatch("VOICE_STATE_UPDATE", state)
        await self._end_voice_session(session, 4014)

    def _bot_voice(
        self,
        guild_id: str,
        channel_id: str | None,
        *,
        self_mute: bool,
        self_deaf: bool,
    ) -> Json:
        return payloads.voice_state(
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=self.bot_user["id"],
            session_id=self._sessions[0].id,
            self_mute=self_mute,
            self_deaf=self_deaf,
            mute=self.mute,
            deaf=self.deaf,
            suppress=self.suppress,
        )

    def _bot_voice_state(self) -> Json:
        for (_, user_id), state in reversed(self.voice_states.items()):
            if user_id == self.bot_user["id"]:
                return state
        raise RuntimeError("the bot is in no voice channel")

    @web.middleware
    async def _rest(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        if not request.path.startswith(API + "/"):
            return await handler(request)
        record = await _record(request)
        request[RECORD] = record
        self.requests.append(record)
        for hold in self._holds:
            if hold.method == request.method and record.path.startswith(
                hold.route_prefix
            ):
                self._holds.remove(hold)
                await hold.release.wait()
                break
        resource = request.match_info.route.resource
        if resource is None:
            return self._unrouted(request.method, record.path)
        route = resource.canonical
        authorization = request.headers.get(hdrs.AUTHORIZATION)
        if "{interaction_token}" not in route and authorization != f"Bot {self.token}":
            return _error(401, 0, "401: Unauthorized")
        window, retry_after = self._take_window(
            request.method, route, request.match_info
        )
        fault = self._take_fault(request.method, record.path)
        if fault is not None:
            return fault(window)
        if retry_after is not None:
            return _rate_limited(window, "user", retry_after)
        response = await handler(request)
        response.headers.update(window)
        return response

    def _unrouted(self, method: str, path: str) -> web.StreamResponse:
        fault = self._take_fault(method, path)
        return _error(404, 0, "404: Not Found") if fault is None else fault({})

    def _take_window(
        self, method: str, route: str, params: Mapping[str, str]
    ) -> tuple[dict[str, str], float | None]:
        bucket = f"{zlib.crc32(f'{method} {route}'.encode()):08x}"
        key = (bucket, "/".join(params.get(name, "") for name in MAJOR_PARAMETERS))
        now = time.monotonic()
        start, used = self._windows.get(key, (-math.inf, 0))
        if now - start >= ROUTE_WINDOW:
            start, used = now, 0
        exhausted = used >= ROUTE_LIMIT
        if not exhausted:
            used += 1
        self._windows[key] = (start, used)
        reset_after = round(start + ROUTE_WINDOW - now, 3)
        headers = {
            "X-RateLimit-Limit": str(ROUTE_LIMIT),
            "X-RateLimit-Remaining": str(ROUTE_LIMIT - used),
            "X-RateLimit-Reset": f"{time.time() + reset_after:.3f}",
            "X-RateLimit-Reset-After": str(reset_after),
            "X-RateLimit-Bucket": bucket,
        }
        return headers, reset_after if exhausted else None

    def _take_fault(self, method: str, path: str) -> Respond | None:
        for fault in self._faults:
            if fault.method == method and path.startswith(fault.route_prefix):
                self._faults.remove(fault)
                return fault.respond
        return None

    async def _gateway_bot(self, _request: web.Request) -> web.Response:
        return _json(payloads.fixture("rest/gateway_bot") | {"url": self.gateway_url})

    async def _application(self, _request: web.Request) -> web.Response:
        application = payloads.fixture("rest/application_me")
        return _json(
            application | {"id": str(self.application_id), "bot": self.bot_user}
        )

    async def _commands(self, request: web.Request) -> web.Response:
        if request.match_info["application_id"] != str(self.application_id):
            return _error(404, 10002, "Unknown Application")
        guild_id = request.match_info.get("guild_id")
        commands = (
            self.commands
            if guild_id is None
            else self.guild_commands.setdefault(guild_id, [])
        )
        if request.method == "PUT":
            body = request[RECORD].json
            known = {
                (command["type"], command["name"]): command["id"]
                for command in commands
            }
            ids = set(known.values())
            unknown = [
                index
                for index, command in enumerate(body)
                if command.get("id") is not None and str(command["id"]) not in ids
            ]
            if unknown:
                return _unknown_command_ids(unknown)
            commands[:] = [self._stored_command(command, known) for command in body]
            self.put_count += 1
        if request.method == "PUT" or _query_flag(request, "with_localizations"):
            return _json(commands)
        return _json(_without_localizations(commands))

    def _stored_command(self, command: Json, known: dict[tuple[int, str], str]) -> Json:
        defaults: Json = {
            "type": 1,
            "default_member_permissions": None,
            "nsfw": False,
            "integration_types": [0],
            "contexts": [0, 1, 2],
        }
        stored = defaults | _localizable(command)
        key = (stored["type"], stored["name"])
        return stored | {
            "id": command.get("id") or known.get(key) or payloads.snowflake(),
            "application_id": str(self.application_id),
            "version": payloads.snowflake(),
        }

    async def _callback(self, request: web.Request) -> web.Response:
        token = request.match_info["interaction_token"]
        interaction = self._interactions.get(token)
        if (
            interaction is None
            or interaction["id"] != request.match_info["interaction_id"]
        ):
            return _error(404, 10062, "Unknown interaction")
        if token in self._responses:
            return _error(400, 40060, "Interaction has already been acknowledged.")
        record = request[RECORD]
        kind = record.json["type"]
        original = self._original_response(
            interaction, kind, record.json.get("data") or {}
        )
        self._responses[token] = {} if original is None else {"@original": original}
        waiter = self._callback_waiters.pop(interaction["id"], None)
        if waiter is not None and not waiter.done():
            waiter.set_result(record)
        if not _query_flag(request, "with_response"):
            return web.Response(status=204)
        return _json(_callback_response(interaction, kind, original))

    def _original_response(
        self, interaction: Json, kind: int, data: Json
    ) -> Json | None:
        match kind:
            case 4:
                return self._interaction_message(interaction, data)
            case 5:
                loading = {"flags": LOADING | data.get("flags", 0)}
                return self._interaction_message(interaction, loading)
            case 6 | 7:
                message: Json = interaction["message"]
                return message | _message_fields(data)
            case _:
                return None

    def _interaction_message(self, interaction: Json, body: Json) -> Json:
        return self._new_message(
            interaction["channel_id"], body, webhook_id=str(self.application_id)
        )

    def _new_message(self, channel_id: str, body: Json, **fields: Any) -> Json:
        message = payloads.message(
            channel_id=channel_id, author=self.bot_user, content="", **fields
        )
        return {
            key: value
            for key, value in (message | _message_fields(body)).items()
            if key not in GATEWAY_ONLY_MESSAGE_FIELDS
        }

    def _webhook(self, request: web.Request) -> dict[str, Json] | None:
        if request.match_info["application_id"] != str(self.application_id):
            return None
        return self._responses.get(request.match_info["interaction_token"])

    async def _followup(self, request: web.Request) -> web.Response:
        messages = self._webhook(request)
        if messages is None:
            return _error(404, 10015, "Unknown Webhook")
        interaction = self._interactions[request.match_info["interaction_token"]]
        message = self._interaction_message(interaction, request[RECORD].json)
        messages[message["id"]] = message
        return _json(message)

    async def _webhook_message(self, request: web.Request) -> web.Response:
        messages = self._webhook(request)
        if messages is None:
            return _error(404, 10015, "Unknown Webhook")
        return _message_route(request, messages)

    async def _create_message(self, request: web.Request) -> web.Response:
        channel_id = request.match_info["channel_id"]
        message = self._new_message(channel_id, request[RECORD].json)
        self.messages.setdefault(channel_id, {})[message["id"]] = message
        return _json(message)

    async def _channel_message(self, request: web.Request) -> web.Response:
        messages = self.messages.get(request.match_info["channel_id"], {})
        return _message_route(request, messages)

    async def _channel(self, request: web.Request) -> web.Response:
        channel = self.channels.get(request.match_info["channel_id"])
        return _found(channel, 10003, "Unknown Channel")

    async def _guild(self, request: web.Request) -> web.Response:
        guild = self.guilds.get(request.match_info["guild_id"])
        return _found(guild, 10004, "Unknown Guild")

    async def _member(self, request: web.Request) -> web.Response:
        key = (request.match_info["guild_id"], request.match_info["user_id"])
        return _found(self.members.get(key), 10007, "Unknown Member")

    async def _voice_state(self, request: web.Request) -> web.Response:
        user_id = request.match_info["user_id"]
        if user_id == "@me":
            user_id = self.bot_user["id"]
        state = self.voice_states.get((request.match_info["guild_id"], user_id))
        return _found(state, 10065, "Unknown Voice State")

    async def _edit_voice_state(self, request: web.Request) -> web.Response:
        state = self.voice_states.get(
            (request.match_info["guild_id"], self.bot_user["id"])
        )
        if state is None:
            return _error(404, 10065, "Unknown Voice State")
        edit: Json = request[RECORD].json or {}
        state |= {
            key: edit[key]
            for key in ("suppress", "request_to_speak_timestamp")
            if key in edit
        }
        await self.dispatch("VOICE_STATE_UPDATE", state)
        return web.Response(status=204)

    async def _user(self, request: web.Request) -> web.Response:
        user_id = request.match_info["user_id"]
        if user_id in ("@me", self.bot_user["id"]):
            return _json(self.bot_user)
        return _found(self.users.get(user_id), 10013, "Unknown User")


async def _record(request: web.Request) -> Recorded:
    parts: list[Part] = []
    body: Any = None
    if request.content_type == "multipart/form-data":
        async for part in await request.multipart():
            if isinstance(part, BodyPartReader):
                content_type = part.headers.get(hdrs.CONTENT_TYPE)
                data = bytes(await part.read())
                parts.append(Part(part.name, part.filename, content_type, data))
        body = next(
            (
                msgspec.json.decode(part.data)
                for part in parts
                if part.name == "payload_json"
            ),
            None,
        )
    elif request.content_type == "application/json":
        body = msgspec.json.decode(await request.read())
    elif request.content_type == "application/x-www-form-urlencoded":
        body = dict(await request.post())
    headers = {name.lower(): value for name, value in request.headers.items()}
    path = request.path.removeprefix(API)
    return Recorded(request.method, path, dict(request.query), headers, body, parts)


def _query_flag(request: web.Request, name: str) -> bool:
    return request.query.get(name) in ("true", "True", "1")


def _localizable(entity: Json) -> Json:
    stored: Json = dict.fromkeys(LOCALIZATIONS)
    for key, value in entity.items():
        if key == "options":
            stored[key] = [_localizable(option) for option in value]
        elif key == "choices":
            stored[key] = [{"name_localizations": None} | choice for choice in value]
        elif key != "required" or value:
            stored[key] = value
    return stored


def _without_localizations(value: Any) -> Any:
    match value:
        case [*items]:
            return [_without_localizations(item) for item in items]
        case {**fields}:
            return {
                key: _without_localizations(item)
                for key, item in fields.items()
                if key not in LOCALIZATIONS
            }
        case _:
            return value


def _message_route(request: web.Request, messages: dict[str, Json]) -> web.Response:
    message_id = request.match_info["message_id"]
    message = messages.get(message_id)
    if message is None:
        return _error(404, 10008, "Unknown Message")
    if request.method == "DELETE":
        del messages[message_id]
        return web.Response(status=204)
    if request.method == "PATCH":
        edit = _message_fields(request[RECORD].json)
        flags = message["flags"] & ~LOADING | edit.get("flags", 0)
        message |= edit | {
            "flags": flags,
            "edited_timestamp": datetime.now(UTC).isoformat(),
        }
    return _json(message)


def _message_fields(body: Json) -> Json:
    fields = {key: body[key] for key in MESSAGE_FIELDS if key in body}
    return fields | {key: CLEARED[key] for key in CLEARED if fields.get(key, 0) is None}


def _callback_response(interaction: Json, kind: int, original: Json | None) -> Json:
    summary: Json = {"id": interaction["id"], "type": interaction["type"]}
    resource: Json = {"type": kind}
    if original is not None:
        summary |= {
            "response_message_id": original["id"],
            "response_message_loading": kind == DEFERRED_WITH_SOURCE,
            "response_message_ephemeral": bool(original["flags"] & EPHEMERAL),
        }
    if kind in (4, 7):
        resource["message"] = original
    if kind == LAUNCH_ACTIVITY:
        summary["activity_instance_id"] = f"i-{interaction['id']}"
        resource["activity_instance"] = {"id": summary["activity_instance_id"]}
    return {"interaction": summary, "resource": resource}


def _rate_limited(
    window: dict[str, str], scope: str, retry_after: float
) -> web.Response:
    headers = {"Retry-After": str(retry_after), "X-RateLimit-Scope": scope}
    match scope:
        case "global":
            headers["X-RateLimit-Global"] = "true"
        case "user":
            spent = {
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset-After": str(retry_after),
            }
            headers = window | headers | spent
        case _:
            headers = window | headers
    text = (
        "The resource is being rate limited."
        if scope == "shared"
        else "You are being rate limited."
    )
    body = {"message": text, "retry_after": retry_after, "global": scope == "global"}
    return _json(body, 429, headers)


def _found(entity: Json | None, code: int, message: str) -> web.Response:
    return _error(404, code, message) if entity is None else _json(entity)


def _error(status: int, code: int, message: str) -> web.Response:
    return _json({"message": message, "code": code}, status)


def _unknown_command_ids(indices: list[int]) -> web.Response:
    error = {
        "code": "APPLICATION_COMMANDS_INVALID_ID",
        "message": "Invalid application command id provided",
    }
    errors = {str(index): {"_errors": [error]} for index in indices}
    body = {"message": "Invalid Form Body", "code": 50035, "errors": errors}
    return _json(body, 400)


def _json(
    body: Any, status: int = 200, headers: Mapping[str, str] | None = None
) -> web.Response:
    return web.Response(
        body=msgspec.json.encode(body),
        status=status,
        content_type="application/json",
        headers=headers,
    )


def _frame(t: str | None, s: int | None, op: int, d: Any) -> bytes:
    return msgspec.json.encode({"t": t, "s": s, "op": op, "d": d})


def _zstd_streams() -> Callable[[], Callable[[bytes], bytes]]:
    if sys.version_info >= (3, 14):
        from compression.zstd import CompressionParameter, ZstdCompressor

        options = {
            CompressionParameter.compression_level: 6,
            CompressionParameter.window_log: 18,
        }

        def stream() -> Callable[[bytes], bytes]:
            compressor = ZstdCompressor(options=options)
            return lambda frame: compressor.compress(frame, ZstdCompressor.FLUSH_BLOCK)

        return stream
    raise RuntimeError("compress=True needs compression.zstd from Python 3.14")
