import secrets
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from typing import Any

import msgspec

from bostie._bridge import Bridge
from bostie.errors import (
    BadRequest,
    BostieError,
    Forbidden,
    HTTPException,
    InvalidToken,
    NotFound,
    RateLimited,
    ServerError,
    UnexpectedResponse,
)
from bostie.file import File
from bostie.models.base import encoder

TOKEN_PLACEHOLDERS = ("{interaction_token}", "{webhook_token}")

type QueryValue = str | int | float | bool
type Query = Mapping[str, QueryValue | Sequence[QueryValue] | None]

STATUS_ERRORS: dict[int, type[HTTPException]] = {
    400: BadRequest,
    403: Forbidden,
    404: NotFound,
}


class _ErrorBody(msgspec.Struct):
    code: int = 0
    message: str = ""
    errors: dict[str, Any] = {}
    retry_after: float | None = None
    is_global: bool = msgspec.field(default=False, name="global")


_error_decoder = msgspec.json.Decoder(_ErrorBody, strict=False)


class HTTPClient:
    """Sends requests to Discord's REST API through bostie's rate limiter."""

    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge

    async def request(
        self,
        method: str,
        route: str,
        /,
        *,
        json: Any = None,
        files: Sequence[File] = (),
        query: Query | None = None,
        reason: str | None = None,
        **params: object,
    ) -> Any:
        """Send a request to a route like ``/channels/{channel_id}``; return its JSON.

        An empty body returns None; an error status raises the matching bostie error.
        """
        body = await self._request(
            method,
            route,
            json=json,
            files=files,
            query=query,
            reason=reason,
            **params,
        )
        return msgspec.json.decode(body) if body else None

    async def _decoded[T](
        self,
        decode: Callable[[bytes], T],
        method: str,
        route: str,
        /,
        **request: Any,
    ) -> T:
        data = await self._request(method, route, **request)
        return decode_answer(decode, data, took_effect=method != "GET")

    async def _request(
        self,
        method: str,
        route: str,
        /,
        *,
        json: Any = None,
        files: Sequence[File] = (),
        query: Query | None = None,
        reason: str | None = None,
        **params: object,
    ) -> bytes:
        content_type = None
        body = None
        if files:
            content_type, body = _multipart(json, files)
        elif json is not None:
            body = encoder.encode(json)
        status, data = await self._bridge.request(
            method,
            route,
            {name: str(value) for name, value in params.items()},
            _query_pairs(query or {}),
            body=body,
            content_type=content_type,
            reason=reason,
        )
        if status >= HTTPStatus.BAD_REQUEST:
            bot_route = not any(token in route for token in TOKEN_PLACEHOLDERS)
            raise _status_error(status, data, bot_route=bot_route)
        return data


def decode_answer[T](
    decode: Callable[[bytes], T], data: bytes, *, took_effect: bool
) -> T:
    try:
        return decode(data)
    except msgspec.DecodeError as error:
        raise UnexpectedResponse(data, str(error), took_effect=took_effect) from error


def _query_pairs(query: Query) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for name, value in query.items():
        if value is not None:
            values = (
                (value,)
                if isinstance(value, str) or not isinstance(value, Sequence)
                else value
            )
            pairs.extend((name, _query_text(item)) for item in values)
    return pairs


def _query_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _multipart(payload: Any, files: Sequence[File]) -> tuple[str, bytes]:
    boundary = secrets.token_hex(16)
    chunks: list[bytes] = []

    def add(disposition: str, content_type: str, data: bytes) -> None:
        head = (
            f"--{boundary}\r\nContent-Disposition: form-data; {disposition}\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        )
        chunks.extend((head.encode(), data, b"\r\n"))

    if payload is not None:
        add('name="payload_json"', "application/json", encoder.encode(payload))
    for index, file in enumerate(files):
        filename = (
            file.name.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")
        )
        disposition = f'name="files[{index}]"; filename="{filename}"'
        add(disposition, "application/octet-stream", file.data)
    chunks.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def _status_error(status: int, body: bytes, *, bot_route: bool) -> BostieError:
    try:
        error = _error_decoder.decode(body)
    except msgspec.DecodeError:
        error = _ErrorBody()
    if status == HTTPStatus.UNAUTHORIZED and bot_route:
        return InvalidToken(error.message or "401: Unauthorized")
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return RateLimited(
            status,
            error.code,
            error.message,
            error.errors,
            body,
            retry_after=error.retry_after,
            is_global=error.is_global,
        )
    exception = (
        ServerError
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR
        else STATUS_ERRORS.get(status, HTTPException)
    )
    return exception(status, error.code, error.message, error.errors, body)
