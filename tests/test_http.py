import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Literal

import msgspec
import pytest
from fakediscord import FakeDiscord, Part
from fakediscord.server import ROUTE_LIMIT

from lyrecord._bridge import Bridge, GatewayDelivery
from lyrecord.errors import (
    BadRequest,
    Forbidden,
    HTTPException,
    InvalidToken,
    NotFound,
    RateLimited,
    ServerError,
)
from lyrecord.file import File
from lyrecord.http import HTTPClient

MESSAGES = "/channels/{channel_id}/messages"

type Scenario = Callable[[FakeDiscord, HTTPClient], Coroutine[Any, Any, None]]


def ignore(delivery: GatewayDelivery, received: float) -> None:
    pass


def run(scenario: Scenario, token: str | None = None) -> None:
    async def main() -> None:
        async with FakeDiscord() as fake:
            bridge = Bridge(token or fake.token, fake.api_url, ignore, global_limit=50)
            async with bridge, asyncio.timeout(10):
                await scenario(fake, HTTPClient(bridge))

    asyncio.run(main())


def channel_id(fake: FakeDiscord) -> str:
    return next(iter(fake.channels))


def test_requests_follow_the_rate_limit_headers_instead_of_hitting_429() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        channel = channel_id(fake)
        for _ in range(ROUTE_LIMIT + 1):
            fetched = await http.request(
                "GET", "/channels/{channel_id}", channel_id=int(channel)
            )

        assert fetched == fake.channels[channel]
        assert len(fake.requests) == ROUTE_LIMIT + 1
        assert fake.requests[0].headers["authorization"] == f"Bot {fake.token}"

    run(scenario)


def test_an_injected_global_rate_limit_is_waited_out_and_retried() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.rate_limit("GET", "/users/@me", scope="global", retry_after=0.2)

        user = await http.request("GET", "/users/@me")

        assert user == fake.bot_user
        assert [request.path for request in fake.requests] == ["/users/@me"] * 2

    run(scenario)


@pytest.mark.parametrize(
    ("status", "body", "error_type"),
    [
        (
            400,
            {
                "message": "Invalid Form Body",
                "code": 50035,
                "errors": {"content": {"_errors": [{"code": "BASE_TYPE_MAX_LENGTH"}]}},
            },
            BadRequest,
        ),
        (403, {"message": "Missing Access", "code": 50001}, Forbidden),
        (404, {"message": "Unknown Channel", "code": 10003}, NotFound),
        (405, {"message": "405: Method Not Allowed", "code": 0}, HTTPException),
        (500, {"message": "500: Internal Server Error", "code": 0}, ServerError),
    ],
)
def test_error_statuses_raise_with_discords_code_message_and_errors(
    status: int, body: dict[str, Any], error_type: type[HTTPException]
) -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        route = f"/channels/{channel_id(fake)}/messages"
        fake.fail("POST", route, status, body)

        with pytest.raises(HTTPException) as raised:
            await http.request(
                "POST", MESSAGES, channel_id=channel_id(fake), json={"content": "x"}
            )

        error = raised.value
        assert type(error) is error_type
        assert (error.status, error.code, error.message) == (
            status,
            body["code"],
            body["message"],
        )
        assert error.errors == body.get("errors", {})
        assert msgspec.json.decode(error.body) == body

    run(scenario)


@pytest.mark.parametrize("scope", ["shared", "global"])
def test_a_rate_limit_longer_than_lyrecord_waits_raises_rate_limited(
    scope: Literal["shared", "global"],
) -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        fake.rate_limit("GET", "/users/@me", scope=scope, retry_after=60)

        with pytest.raises(RateLimited) as raised:
            await http.request("GET", "/users/@me")

        assert raised.value.status == 429
        assert raised.value.retry_after == 60
        assert raised.value.is_global is (scope == "global")

    run(scenario)


def test_a_rejected_interaction_token_keeps_discords_status_and_code() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        route = f"/webhooks/{fake.application_id}/dead"
        fake.fail(
            "POST", route, 401, {"message": "Invalid Webhook Token", "code": 50027}
        )

        with pytest.raises(HTTPException) as raised:
            await http.request(
                "POST",
                "/webhooks/{application_id}/{interaction_token}",
                application_id=fake.application_id,
                interaction_token="dead",
                json={"content": "x"},
            )

        assert type(raised.value) is HTTPException
        assert (raised.value.status, raised.value.code) == (401, 50027)
        assert "authorization" not in fake.requests[0].headers

    run(scenario)


def test_a_rejected_token_raises_invalid_token() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        for _ in range(2):
            with pytest.raises(InvalidToken):
                await http.request("GET", "/users/@me")

        assert len(fake.requests) == 1

    run(scenario, token="wrong-token")


def test_files_go_out_as_multipart_with_payload_json_and_escaped_names() -> None:
    async def scenario(fake: FakeDiscord, http: HTTPClient) -> None:
        payload = {"content": "logs", "attachments": [{"id": 0, "filename": "a.txt"}]}

        message = await http.request(
            "POST",
            MESSAGES,
            channel_id=channel_id(fake),
            json=payload,
            files=[File('a"b\r\n.txt', b"all good"), File("raw.bin", b"\x00\r\n--")],
        )

        sent = fake.requests[-1]
        assert message["content"] == "logs"
        assert sent.headers["content-type"].startswith("multipart/form-data; boundary=")
        assert sent.json == payload
        assert sent.parts == [
            Part(
                "payload_json", None, "application/json", msgspec.json.encode(payload)
            ),
            Part(
                "files[0]", "a%22b%0D%0A.txt", "application/octet-stream", b"all good"
            ),
            Part("files[1]", "raw.bin", "application/octet-stream", b"\x00\r\n--"),
        ]

    run(scenario)


def test_params_and_query_values_are_sent_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[Any, ...]] = []

    async def record(*request: Any) -> tuple[int, bytes]:
        sent.append(request)
        return 204, b""

    async def main() -> None:
        bridge = Bridge("token", "http://127.0.0.1:1/api/v10", ignore, global_limit=50)
        monkeypatch.setattr(bridge, "request", record)
        result = await HTTPClient(bridge).request(
            "GET",
            "/guilds/{guild_id}/members",
            guild_id=123,
            query={"limit": 5, "with_counts": True, "after": None, "ids": [1, 2]},
            reason="audit",
        )
        await bridge.close()

        assert result is None
        assert sent == [
            (
                "GET",
                "/guilds/{guild_id}/members",
                {"guild_id": "123"},
                [("limit", "5"), ("with_counts", "true"), ("ids", "1"), ("ids", "2")],
                None,
                None,
                "audit",
            )
        ]

    asyncio.run(main())
