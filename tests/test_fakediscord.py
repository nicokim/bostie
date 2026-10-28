import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from typing import Any

import aiohttp
import msgspec
import pytest
from fakediscord import FakeDiscord, payloads
from fakediscord.payloads import DISCORD_EPOCH_MS, Json
from fakediscord.server import EPHEMERAL, LOADING, ROUTE_LIMIT

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
            fake.hold_acks(True)
            await ws.send_json({"op": 1, "d": 1})
            assert await received_ops(fake, 3) == [2, 1, 1]
            seq = await fake.dispatch("MESSAGE_CREATE", payloads.message())
            after_held_beat = await receive(ws)

        assert hello.startswith(
            '{"t":null,"s":null,"op":10,"d":{"heartbeat_interval":500,'
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


def test_close_and_drop_end_the_current_connection() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with session.ws_connect(fake.gateway_url) as ws:
            await ws.receive_str()
            await fake.close_gateway(4014)
            closed = await ws.receive()
        async with session.ws_connect(fake.gateway_url) as ws:
            await ws.receive_str()
            fake.drop_gateway()
            dropped = await ws.receive()

        assert (closed.type, closed.data) == (aiohttp.WSMsgType.CLOSE, 4014)
        assert dropped.type is not aiohttp.WSMsgType.CLOSE

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


def test_interaction_returns_its_first_callback_and_keeps_the_response() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with session.ws_connect(fake.gateway_url) as ws:
            await handshake(fake, ws)
            data = payloads.command("roll", payloads.option("sides", 4, 20))
            pending = asyncio.ensure_future(fake.interaction(2, data))
            event = (await receive(ws))["d"]
            route = (
                f"{fake.api_url}/interactions/{event['id']}/{event['token']}/callback"
            )
            body = {"type": 4, "data": {"content": "rolled 7"}}
            async with session.post(
                route, params={"with_response": "true"}, json=body
            ) as r:
                created = await r.json()
            recorded = await pending
            async with session.post(route, json=body) as r:
                again = (r.status, await r.json())
            async with session.post(route.replace(event["id"], "1"), json=body) as r:
                unknown = (r.status, await r.json())
            original = (
                f"{fake.api_url}/webhooks/{fake.application_id}/{event['token']}"
                "/messages/@original"
            )
            async with session.patch(original, json={"content": "rerolled"}) as r:
                edited = await r.json()
            async with session.delete(original) as r:
                deleted = r.status
            async with session.get(original) as r:
                gone = (r.status, await r.json())

        assert event["data"] == data
        assert (recorded.json, recorded.query) == (body, {"with_response": "true"})
        assert fake.callbacks.get_nowait() is recorded
        message = created["resource"]["message"]
        assert (message["content"], message["author"]) == ("rolled 7", fake.bot_user)
        assert created["interaction"]["response_message_id"] == message["id"]
        assert again == (
            400,
            {"message": "Interaction has already been acknowledged.", "code": 40060},
        )
        assert unknown[0] == 404
        assert (edited["id"], edited["content"]) == (message["id"], "rerolled")
        assert deleted == 204
        assert gone == (404, {"message": "Unknown Message", "code": 10008})

    run(scenario)


def test_deferred_response_loads_until_edited_and_takes_followups() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with session.ws_connect(fake.gateway_url) as ws:
            await handshake(fake, ws)
            event = payloads.interaction(2, payloads.command("slow"))
            webhook = f"{fake.api_url}/webhooks/{fake.application_id}/{event['token']}"
            async with session.post(webhook, json={"content": "too early"}) as r:
                early = r.status
            await fake.dispatch("INTERACTION_CREATE", event)
            route = (
                f"{fake.api_url}/interactions/{event['id']}/{event['token']}/callback"
            )
            async with session.post(
                route, json={"type": 5, "data": {"flags": EPHEMERAL}}
            ) as r:
                deferred = r.status
            async with session.get(webhook + "/messages/@original") as r:
                loading = await r.json()
            async with session.patch(
                webhook + "/messages/@original", json={"content": "done"}
            ) as r:
                edited = await r.json()
            async with session.post(webhook, json={"content": "one more"}) as r:
                followup = await r.json()
            other_app = webhook.replace(str(fake.application_id), "1")
            async with session.post(other_app, json={"content": "lost"}) as r:
                wrong_app = r.status

        assert (early, deferred, wrong_app) == (404, 204, 404)
        assert loading["flags"] == LOADING | EPHEMERAL
        assert (edited["content"], edited["flags"]) == ("done", EPHEMERAL)
        assert followup["content"] == "one more"
        assert followup["channel_id"] == event["channel_id"]

    run(scenario)


def test_component_update_edits_the_message_it_came_from() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        async with session.ws_connect(fake.gateway_url) as ws:
            await handshake(fake, ws)
            event = payloads.interaction(3, payloads.button("vote:rules:1"))
            await fake.dispatch("INTERACTION_CREATE", event)
            route = (
                f"{fake.api_url}/interactions/{event['id']}/{event['token']}/callback"
            )
            body = {"type": 7, "data": {"content": "voted", "components": []}}
            async with session.post(
                route, params={"with_response": "true"}, json=body
            ) as r:
                updated = (await r.json())["resource"]["message"]

        assert event["data"]["custom_id"] == "vote:rules:1"
        assert updated == event["message"] | {"content": "voted", "components": []}

    run(scenario)


def test_route_windows_are_counted_per_major_parameter() -> None:
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
        assert 0 < body["retry_after"] <= 1
        assert headers["Retry-After"] == str(body["retry_after"])
        assert responses[-1][0] == 404
        assert remaining[-1] == str(ROUTE_LIMIT - 1)
        assert len({headers["X-RateLimit-Bucket"] for _, headers, _ in responses}) == 1

    run(scenario)


@pytest.mark.parametrize(
    ("scope", "remaining", "global_header"),
    [
        ("user", "0", None),
        ("shared", str(ROUTE_LIMIT - 1), None),
        ("global", None, "true"),
    ],
)
def test_injected_rate_limit_answers_with_its_scope_then_lets_requests_through(
    scope: Any, remaining: str | None, global_header: str | None
) -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        fake.rate_limit("GET", "/gateway/bot", scope=scope, retry_after=0.25)
        url = f"{fake.api_url}/gateway/bot"
        async with session.get(url, headers=auth(fake)) as r:
            limited = (r.status, r.headers.copy(), await r.json())
        async with session.get(url, headers=auth(fake)) as r:
            passed = (r.status, await r.json())

        status, headers, body = limited
        assert (status, headers["X-RateLimit-Scope"], headers["Retry-After"]) == (
            429,
            scope,
            "0.25",
        )
        assert headers.get("X-RateLimit-Remaining") == remaining
        assert headers.get("X-RateLimit-Global") == global_header
        assert (body["retry_after"], body["global"]) == (0.25, scope == "global")
        assert passed == (
            200,
            payloads.fixture("rest/gateway_bot") | {"url": fake.gateway_url},
        )

    run(scenario)


def test_injected_failure_repeats_then_requests_reach_the_route() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        forbidden = payloads.fixture("rest/error_403")
        fake.fail("GET", "/channels/", 403, forbidden, times=2)
        results = []
        for _ in range(3):
            url = f"{fake.api_url}/channels/{payloads.channel()['id']}"
            async with session.get(url, headers=auth(fake)) as r:
                results.append((r.status, await r.json()))

        assert results == [
            (403, forbidden),
            (403, forbidden),
            (200, payloads.channel()),
        ]

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


def test_unknown_routes_and_missing_tokens_get_discord_errors() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        results = []
        for method, path, headers in [
            ("GET", "/nowhere", auth(fake)),
            ("POST", "/gateway/bot", auth(fake)),
            ("GET", "/gateway/bot", {}),
        ]:
            async with session.request(
                method, fake.api_url + path, headers=headers
            ) as r:
                results.append((r.status, await r.json()))

        assert results == [
            (404, {"message": "404: Not Found", "code": 0}),
            (404, {"message": "404: Not Found", "code": 0}),
            (401, {"message": "401: Unauthorized", "code": 0}),
        ]
        assert [(r.method, r.path) for r in fake.requests] == [
            ("GET", "/nowhere"),
            ("POST", "/gateway/bot"),
            ("GET", "/gateway/bot"),
        ]

    run(scenario)


def test_multipart_message_records_its_parts_and_is_stored() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        form = aiohttp.FormData()
        form.add_field(
            "payload_json",
            '{"content":"log attached"}',
            content_type="application/json",
        )
        form.add_field(
            "files[0]", b"line 1\n", filename="log.txt", content_type="text/plain"
        )
        url = f"{fake.api_url}/channels/{payloads.channel()['id']}/messages"
        async with session.post(url, data=form, headers=auth(fake)) as r:
            created = await r.json()
        async with session.get(f"{url}/{created['id']}", headers=auth(fake)) as r:
            fetched = await r.json()

        recorded = fake.requests[0]
        assert recorded.json == {"content": "log attached"}
        assert [
            (p.name, p.filename, p.content_type, p.data) for p in recorded.parts
        ] == [
            ("payload_json", None, "application/json", b'{"content":"log attached"}'),
            ("files[0]", "log.txt", "text/plain", b"line 1\n"),
        ]
        assert (created["content"], created["author"]) == (
            "log attached",
            fake.bot_user,
        )
        assert "member" not in created
        assert fetched == created

    run(scenario)


def test_command_overwrite_keeps_ids_by_name_and_scope() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        guild_id = payloads.guild()["id"]
        app = f"{fake.api_url}/applications/{fake.application_id}"
        guild_route = f"{app}/guilds/{guild_id}/commands"
        first = [{"name": "roll", "description": "Roll"}]
        second = [
            {"name": "roll", "description": "Roll a die"},
            {"name": "ping", "description": "Ping"},
        ]
        stored = []
        for commands in (first, second):
            async with session.put(guild_route, json=commands, headers=auth(fake)) as r:
                stored.append(await r.json())
        async with session.get(
            guild_route, params={"with_localizations": "true"}, headers=auth(fake)
        ) as r:
            fetched = await r.json()
        async with session.get(f"{app}/commands", headers=auth(fake)) as r:
            global_commands = await r.json()
        other_app = f"{fake.api_url}/applications/1/commands"
        async with session.put(other_app, json=first, headers=auth(fake)) as r:
            unknown_app = (r.status, await r.json())

        assert stored[1][0]["id"] == stored[0][0]["id"] != stored[1][1]["id"]
        assert stored[1][0]["guild_id"] == guild_id
        assert "contexts" not in stored[1][0]
        assert fetched == stored[1]
        assert global_commands == []
        assert fake.put_count == 2
        assert unknown_app == (404, {"message": "Unknown Application", "code": 10002})

    run(scenario)


def test_commands_come_back_in_discords_shape() -> None:
    async def scenario(fake: FakeDiscord, session: aiohttp.ClientSession) -> None:
        route = f"{fake.api_url}/applications/{fake.application_id}/commands"
        sides = {
            "type": 4,
            "name": "sides",
            "description": "Sides",
            "required": False,
            "choices": [{"name": "Six", "value": 6}],
        }
        commands = [
            {
                "name": "roll",
                "description": "Roll",
                "contexts": [0],
                "options": [sides],
            },
            {"type": 2, "name": "Info", "name_localizations": {"de": "Infos"}},
        ]
        async with session.put(route, json=commands, headers=auth(fake)) as r:
            put = await r.json()
        async with session.get(
            route, params={"with_localizations": "True"}, headers=auth(fake)
        ) as r:
            localized = await r.json()
        async with session.get(route, headers=auth(fake)) as r:
            plain = await r.json()

        assert put[0] | {"id": None, "version": None} == {
            "id": None,
            "application_id": str(fake.application_id),
            "version": None,
            "type": 1,
            "name": "roll",
            "name_localizations": None,
            "description": "Roll",
            "description_localizations": None,
            "default_member_permissions": None,
            "nsfw": False,
            "integration_types": [0],
            "contexts": [0],
            "options": [
                {
                    "type": 4,
                    "name": "sides",
                    "name_localizations": None,
                    "description": "Sides",
                    "description_localizations": None,
                    "choices": [
                        {"name": "Six", "name_localizations": None, "value": 6}
                    ],
                }
            ],
        }
        assert (put[1]["contexts"], put[1]["name_localizations"]) == (
            [0, 1, 2],
            {"de": "Infos"},
        )
        assert localized == put
        assert plain[0]["options"] == [
            {
                "type": 4,
                "name": "sides",
                "description": "Sides",
                "choices": [{"name": "Six", "value": 6}],
            }
        ]
        assert not {"name_localizations", "description_localizations"} & (
            plain[0].keys() | plain[1].keys()
        )

    run(scenario)


def test_snowflakes_encode_their_time_and_stay_unique() -> None:
    at = 1_800_000_000.5
    ids = [payloads.snowflake(at) for _ in range(100)]

    assert len(set(ids)) == 100
    assert {(int(i) >> 22) + DISCORD_EPOCH_MS for i in ids} == {1_800_000_000_500}


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
