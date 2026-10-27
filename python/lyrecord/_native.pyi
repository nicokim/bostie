from typing import Literal, final

__all__ = ["Core"]

type ResponseMessage = tuple[
    Literal["response"], int, int, list[tuple[str, str]], bytes
]
type FailureMessage = tuple[Literal["failure"], int, str]
type Message = ResponseMessage | FailureMessage

@final
class Core:
    @staticmethod
    def create() -> tuple[Core, int]: ...
    def submit_http(
        self,
        method: str,
        url: str,
        headers: list[tuple[str, str]],
        body: bytes | None = None,
    ) -> int: ...
    def cancel(self, ticket: int) -> None: ...
    def drain(self) -> list[Message]: ...
    def inflight(self) -> int: ...
    def close(self) -> None: ...
