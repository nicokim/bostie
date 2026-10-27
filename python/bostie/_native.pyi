from typing import Literal, final

__all__ = ["Core"]

type FailureKind = Literal["transport", "invalid_token", "request_budget", "bug"]

type ResponseDelivery = tuple[Literal["response"], int, int, bytes]
type FailureDelivery = tuple[Literal["failure"], int, FailureKind, str]
type DispatchDelivery = tuple[Literal["dispatch"], int, int, str, bytes]
type DisconnectedDelivery = tuple[Literal["disconnected"], int, int | None, str, bool]
type StoppedDelivery = tuple[Literal["stopped"], int, int | None, str]
type BackpressureDelivery = tuple[Literal["backpressure"], int, int]
type InvalidRequestsDelivery = tuple[Literal["invalid_requests"], int]
type Delivery = (
    ResponseDelivery
    | FailureDelivery
    | DispatchDelivery
    | DisconnectedDelivery
    | StoppedDelivery
    | BackpressureDelivery
    | InvalidRequestsDelivery
)

@final
class Core:
    @staticmethod
    def create(token: str, api_url: str, global_limit: int) -> tuple[Core, int]: ...
    def request(
        self,
        method: str,
        route: str,
        params: dict[str, str],
        query: list[tuple[str, str]],
        body: bytes | None = None,
        content_type: str | None = None,
        reason: str | None = None,
    ) -> int: ...
    def gateway_identify_limit(
        self, remaining: int, total: int, reset_after: float
    ) -> None: ...
    def gateway_start(self, shard_id: int, url: str, identify: bytes) -> None: ...
    def gateway_send(self, shard_id: int, payload: bytes) -> None: ...
    def gateway_filter(self, names: list[str] | None) -> None: ...
    def gateway_reconnect(self, shard_id: int) -> None: ...
    def gateway_close(self, shard_id: int) -> None: ...
    def gateway_latency(self, shard_id: int) -> float | None: ...
    def cancel(self, ticket: int) -> None: ...
    def drain(self) -> list[Delivery]: ...
    def inflight(self) -> int: ...
    def close(self) -> None: ...
