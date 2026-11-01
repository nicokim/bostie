from typing import Any, override

import msgspec

from bostie.enums import DisconnectReason


class BostieError(Exception):
    """Base class of every error bostie raises."""


class TransportError(BostieError):
    """A request failed before Discord answered it."""


class InvalidToken(BostieError):
    """Discord rejected the bot token."""


class RequestBudgetExhausted(BostieError):
    """bostie refused a request because too many recent requests were invalid."""


class HTTPException(BostieError):
    """A request ended with an error status."""

    def __init__(
        self,
        status: int,
        code: int,
        message: str,
        errors: dict[str, Any],
        body: bytes,
    ) -> None:
        super().__init__(f"{status} {message} (error code {code})")
        self.status = status
        self.code = code
        self.message = message
        self.errors = errors
        self.body = body


class BadRequest(HTTPException):
    """Discord answered 400 Bad Request."""


class Forbidden(HTTPException):
    """Discord answered 403 Forbidden."""


class NotFound(HTTPException):
    """Discord answered 404 Not Found."""


class RateLimited(HTTPException):
    """Discord answered 429, or bostie held the request back unsent.

    bostie holds it back when the route stays limited for longer than it waits.
    """

    def __init__(
        self,
        status: int,
        code: int,
        message: str,
        errors: dict[str, Any],
        body: bytes,
        *,
        retry_after: float | None,
        is_global: bool,
    ) -> None:
        super().__init__(status, code, message, errors, body)
        self.retry_after = retry_after
        self.is_global = is_global


class ServerError(HTTPException):
    """Discord answered with a 5xx status."""


class UnexpectedResponse(BostieError):
    """Discord accepted a request, but its answer does not decode; see `raw`."""

    def __init__(self, raw_json: bytes, detail: str, *, took_effect: bool) -> None:
        accepted = (
            "Discord accepted the request and it took effect"
            if took_effect
            else "Discord answered the request"
        )
        super().__init__(f"{accepted}, but its answer does not decode: {detail}")
        self.raw_json = raw_json

    @property
    def raw(self) -> Any:
        """Discord's answer as JSON, or None if it was empty."""
        return msgspec.json.decode(self.raw_json) if self.raw_json else None


class GatewayClosed(BostieError):
    """Discord closed the gateway connection and bostie will not reconnect."""

    def __init__(self, code: int | None, reason: str) -> None:
        super().__init__(code, reason)
        self.code = code
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"the gateway closed the connection with code {self.code}: {self.reason}"


class PrivilegedIntentsRequired(GatewayClosed):
    """The bot asked for a privileged intent that is not enabled for it."""

    @override
    def __str__(self) -> str:
        return (
            f"{super().__str__()}; enable the intents the bot requests on its Bot page "
            "in the Discord Developer Portal, under Privileged Gateway Intents"
        )


class ShardingRequired(GatewayClosed):
    """The bot is in too many guilds to run on a single shard."""


class CommandError(BostieError):
    """Base class of errors about application commands and interactions."""


class CommandDefinitionError(CommandError, TypeError):
    """A command, group or option is defined in a way Discord does not accept."""


class CommandSyncError(CommandError):
    """Discord rejected the commands sent during a sync."""

    def __init__(self, command: str, option_path: str, discord_message: str) -> None:
        super().__init__(
            ": ".join(part for part in (command, option_path, discord_message) if part)
        )
        self.command = command
        self.option_path = option_path
        self.discord_message = discord_message


class OptionConversionError(CommandError):
    """An option value could not be converted to its annotated type."""


class InteractionResponded(CommandError):
    """The interaction is not in a state that allows this response."""


class InteractionNotAnswered(CommandError):
    """The interaction needs an answer or a defer before this call."""


class InteractionExpired(CommandError):
    """The interaction can no longer be answered.

    It is over 15 minutes old, or Discord already rejected or forgot its token.
    """


class VoiceError(BostieError):
    """Base class of voice errors."""


class VoiceConnectError(VoiceError):
    """A voice connection ended before it was ready."""

    def __init__(self, reason: DisconnectReason, code: int | None, detail: str) -> None:
        super().__init__(reason, code, detail)
        self.reason = reason
        self.code = code
        self.detail = detail

    @override
    def __str__(self) -> str:
        code = "" if self.code is None else f" (close code {self.code})"
        detail = f": {self.detail}" if self.detail else ""
        return f"voice connection failed: {self.reason}{code}{detail}"


class NotInVoice(VoiceError):
    """The user is not in a voice channel of this guild."""


class VoiceNotConnected(VoiceError):
    """The voice connection has ended."""


class SinkClosed(VoiceError):
    """The sink is closed."""


class BufferFull(VoiceError):
    """The sink has no room for this write."""


class Empty(VoiceError):
    """Nothing is queued in the receiver."""


class ReceiverClosed(VoiceError):
    """The receiver ended and has nothing left to read."""


class SourceError(VoiceError):
    """A source failed to open, decode or seek."""

    def __init__(self, source: str, detail: str) -> None:
        super().__init__(source, detail)
        self.source = source
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.source}: {self.detail}"
