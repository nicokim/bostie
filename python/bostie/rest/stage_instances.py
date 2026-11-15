from typing import Required, TypedDict, Unpack

from bostie.enums import StagePrivacyLevel
from bostie.models import StageInstance
from bostie.rest._base import ResourceGroup, wire


class StageInstanceCreate(TypedDict, total=False):
    channel_id: Required[int]
    topic: Required[str]
    privacy_level: StagePrivacyLevel
    send_start_notification: bool
    guild_scheduled_event_id: int


class StageInstanceEdit(TypedDict, total=False):
    topic: str
    privacy_level: StagePrivacyLevel


class StageInstances(ResourceGroup):
    async def create(
        self, *, reason: str | None = None, **options: Unpack[StageInstanceCreate]
    ) -> StageInstance:
        return await self._call(
            StageInstance,
            "POST",
            "/stage-instances",
            json=wire(options, StageInstanceCreate),
            reason=reason,
        )

    async def delete(self, channel_id: int, /, *, reason: str | None = None) -> None:
        await self._http.send(
            "DELETE",
            "/stage-instances/{channel_id}",
            reason=reason,
            channel_id=channel_id,
        )

    async def edit(
        self,
        channel_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[StageInstanceEdit],
    ) -> StageInstance:
        return await self._call(
            StageInstance,
            "PATCH",
            "/stage-instances/{channel_id}",
            json=wire(options, StageInstanceEdit),
            reason=reason,
            channel_id=channel_id,
        )

    async def fetch(self, channel_id: int, /) -> StageInstance:
        return await self._call(
            StageInstance,
            "GET",
            "/stage-instances/{channel_id}",
            channel_id=channel_id,
        )
