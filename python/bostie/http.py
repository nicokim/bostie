import math
import mimetypes
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import msgspec

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

if TYPE_CHECKING:
    from bostie._bridge import Bridge

TOKEN_PLACEHOLDERS = ("{interaction_token}", "{webhook_token}")
OAUTH2_TOKEN_ROUTES = ("/oauth2/token", "/oauth2/token/revoke")
MAX_REASON = 512

type QueryValue = str | int | float | bool | datetime
type Query = Mapping[str, QueryValue | Sequence[QueryValue] | None]

STATUS_ERRORS: dict[int, type[HTTPException]] = {
    400: BadRequest,
    403: Forbidden,
    404: NotFound,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class HTTPSettings:
    """How bostie paces, retries and times out its REST requests."""

    global_rate_limit: int = 50
    max_rate_limit_wait: float = 30.0
    retries: int = 2
    connect_timeout: float = 10.0
    read_timeout: float = 60.0

    def __post_init__(self) -> None:
        if self.global_rate_limit < 1:
            raise ValueError(
                f"global_rate_limit must be at least 1, got {self.global_rate_limit}"
            )
        if self.retries < 0:
            raise ValueError(f"retries must be at least 0, got {self.retries}")
        for name in ("max_rate_limit_wait", "connect_timeout", "read_timeout"):
            seconds: float = getattr(self, name)
            if not (math.isfinite(seconds) and seconds >= 0):
                raise ValueError(
                    f"{name} must be a number of seconds of at least 0, got {seconds}"
                )


DEFAULT_HTTP = HTTPSettings()


class _ErrorBody(msgspec.Struct):
    code: int = 0
    message: str = ""
    error: str = ""
    error_description: str = ""
    errors: dict[str, Any] = {}
    retry_after: float | None = None
    is_global: bool = msgspec.field(default=False, name="global")


_error_decoder = msgspec.json.Decoder(_ErrorBody, strict=False)


class HTTPClient:
    """Sends requests to Discord's REST API through bostie's rate limiter."""

    def __init__(self, bridge: "Bridge") -> None:
        self._bridge = bridge

    async def request(
        self,
        method: str,
        route: str,
        /,
        *,
        json: Any = None,
        files: Sequence[File] = (),
        form: Mapping[str, object] | None = None,
        urlencoded: Mapping[str, str] | None = None,
        query: Query | None = None,
        reason: str | None = None,
        **params: object,
    ) -> Any:
        """Send a request to a route like ``/channels/{channel_id}``; return its JSON.

        `form` adds named form fields, and files under their own names, to the
        multipart body; `urlencoded` sends fields as a form-urlencoded body. An empty
        body returns None; an error status raises the matching bostie error.
        """
        body = await self._request(
            method,
            route,
            json=json,
            files=files,
            form=form,
            urlencoded=urlencoded,
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
        form: Mapping[str, object] | None = None,
        urlencoded: Mapping[str, str] | None = None,
        query: Query | None = None,
        reason: str | None = None,
        **params: object,
    ) -> bytes:
        if reason is not None and not 1 <= len(reason) <= MAX_REASON:
            raise ValueError(
                f"reason must be 1 to {MAX_REASON} characters, got {len(reason)}"
            )
        content_type = None
        body = None
        parts: dict[str, object] = {
            f"files[{index}]": file for index, file in enumerate(files)
        }
        parts.update(form or {})
        if parts:
            content_type, body = _multipart(json, parts)
        elif urlencoded is not None:
            content_type = "application/x-www-form-urlencoded"
            body = urlencode(urlencoded).encode()
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
            bot_route = route not in OAUTH2_TOKEN_ROUTES and not any(
                token in route for token in TOKEN_PLACEHOLDERS
            )
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
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _multipart(payload: Any, parts: Mapping[str, object]) -> tuple[str, bytes]:
    boundary = secrets.token_hex(16)
    chunks: list[bytes] = []

    def add(disposition: str, content_type: str | None, data: bytes) -> None:
        head = f"--{boundary}\r\nContent-Disposition: form-data; {disposition}\r\n"
        if content_type is not None:
            head += f"Content-Type: {content_type}\r\n"
        chunks.extend((f"{head}\r\n".encode(), data, b"\r\n"))

    if payload is not None:
        add('name="payload_json"', "application/json", encoder.encode(payload))
    for name, value in parts.items():
        if isinstance(value, File):
            filename = (
                value.name.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")
            )
            kind = mimetypes.guess_type(value.name)[0] or "application/octet-stream"
            add(f'name="{name}"; filename="{filename}"', kind, value.data)
        else:
            add(f'name="{name}"', None, str(value).encode())
    chunks.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def _status_error(status: int, body: bytes, *, bot_route: bool) -> BostieError:
    try:
        error = _error_decoder.decode(body)
    except msgspec.DecodeError:
        error = _ErrorBody()
    error.message = error.message or error.error_description or error.error
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
