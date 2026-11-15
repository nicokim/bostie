from collections.abc import AsyncIterator
from datetime import datetime
from typing import Required, TypedDict, Unpack

from bostie.enums import (
    ScheduledEventEntityType,
    ScheduledEventPrivacyLevel,
    ScheduledEventStatus,
)
from bostie.file import File
from bostie.models import (
    RecurrenceRule,
    ScheduledEvent,
    ScheduledEventEntityMetadata,
    ScheduledEventUser,
)
from bostie.rest.base import ResourceGroup, each, wire


class ScheduledEventCreate(TypedDict, total=False):
    channel_id: int
    entity_metadata: ScheduledEventEntityMetadata
    name: Required[str]
    privacy_level: Required[ScheduledEventPrivacyLevel]
    scheduled_start_time: Required[datetime]
    scheduled_end_time: datetime
    description: str
    entity_type: Required[ScheduledEventEntityType]
    image: File
    recurrence_rule: RecurrenceRule


class ScheduledEventEdit(TypedDict, total=False):
    channel_id: int | None
    entity_metadata: ScheduledEventEntityMetadata | None
    name: str
    privacy_level: ScheduledEventPrivacyLevel
    scheduled_start_time: datetime
    scheduled_end_time: datetime
    description: str | None
    entity_type: ScheduledEventEntityType
    status: ScheduledEventStatus
    image: File
    recurrence_rule: RecurrenceRule | None


class ScheduledEventFetch(TypedDict, total=False):
    with_user_count: bool


class ScheduledEventList(TypedDict, total=False):
    with_user_count: bool


class ScheduledEventListUsers(TypedDict, total=False):
    before: int
    after: int
    limit: int
    with_member: bool


class ScheduledEvents(ResourceGroup):
    async def create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ScheduledEventCreate],
    ) -> ScheduledEvent:
        return await self._call(
            ScheduledEvent,
            "POST",
            "/guilds/{guild_id}/scheduled-events",
            json=wire(options, ScheduledEventCreate),
            reason=reason,
            guild_id=guild_id,
        )

    async def delete(self, guild_id: int, guild_scheduled_event_id: int, /) -> None:
        await self._http.send(
            "DELETE",
            "/guilds/{guild_id}/scheduled-events/{guild_scheduled_event_id}",
            guild_id=guild_id,
            guild_scheduled_event_id=guild_scheduled_event_id,
        )

    async def edit(
        self,
        guild_id: int,
        guild_scheduled_event_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[ScheduledEventEdit],
    ) -> ScheduledEvent:
        """Change an event; start or end it by setting its `status`."""
        return await self._call(
            ScheduledEvent,
            "PATCH",
            "/guilds/{guild_id}/scheduled-events/{guild_scheduled_event_id}",
            json=wire(options, ScheduledEventEdit),
            reason=reason,
            guild_id=guild_id,
            guild_scheduled_event_id=guild_scheduled_event_id,
        )

    async def fetch(
        self,
        guild_id: int,
        guild_scheduled_event_id: int,
        /,
        **options: Unpack[ScheduledEventFetch],
    ) -> ScheduledEvent:
        return await self._call(
            ScheduledEvent,
            "GET",
            "/guilds/{guild_id}/scheduled-events/{guild_scheduled_event_id}",
            query=wire(options, ScheduledEventFetch),
            guild_id=guild_id,
            guild_scheduled_event_id=guild_scheduled_event_id,
        )

    async def list(
        self, guild_id: int, /, **options: Unpack[ScheduledEventList]
    ) -> tuple[ScheduledEvent, ...]:
        return await self._call(
            each(ScheduledEvent),
            "GET",
            "/guilds/{guild_id}/scheduled-events",
            query=wire(options, ScheduledEventList),
            guild_id=guild_id,
        )

    def list_users(
        self,
        guild_id: int,
        guild_scheduled_event_id: int,
        /,
        **options: Unpack[ScheduledEventListUsers],
    ) -> AsyncIterator[ScheduledEventUser]:
        """Walk an event's subscribers by user id."""
        pages = self._paginate(
            "/guilds/{guild_id}/scheduled-events/{guild_scheduled_event_id}/users",
            wire(options, ScheduledEventListUsers),
            decodes=each(ScheduledEventUser),
            items=tuple,
            cursor=lambda subscriber: subscriber.user.id,
            page_size=100,
            forward=True,
            guild_id=guild_id,
            guild_scheduled_event_id=guild_scheduled_event_id,
        )
        return (subscriber async for page in pages for subscriber in page)
