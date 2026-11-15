from collections.abc import Sequence
from typing import Required, TypedDict, Unpack

from bostie.enums import AutoModerationEventType, AutoModerationTriggerType
from bostie.models import (
    AutoModerationAction,
    AutoModerationRule,
    AutoModerationTriggerMetadata,
)
from bostie.rest._base import ResourceGroup, each, wire


class AutoModerationCreate(TypedDict, total=False):
    name: Required[str]
    event_type: Required[AutoModerationEventType]
    trigger_type: Required[AutoModerationTriggerType]
    trigger_metadata: AutoModerationTriggerMetadata
    actions: Required[Sequence[AutoModerationAction]]
    enabled: bool
    exempt_roles: Sequence[int]
    exempt_channels: Sequence[int]


class AutoModerationEdit(TypedDict, total=False):
    name: str
    event_type: AutoModerationEventType
    trigger_metadata: AutoModerationTriggerMetadata
    actions: Sequence[AutoModerationAction]
    enabled: bool
    exempt_roles: Sequence[int]
    exempt_channels: Sequence[int]


class AutoModeration(ResourceGroup):
    async def create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[AutoModerationCreate],
    ) -> AutoModerationRule:
        return await self._call(
            AutoModerationRule,
            "POST",
            "/guilds/{guild_id}/auto-moderation/rules",
            json=wire(options, AutoModerationCreate),
            reason=reason,
            guild_id=guild_id,
        )

    async def delete(
        self, guild_id: int, rule_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http.send(
            "DELETE",
            "/guilds/{guild_id}/auto-moderation/rules/{rule_id}",
            reason=reason,
            guild_id=guild_id,
            rule_id=rule_id,
        )

    async def edit(
        self,
        guild_id: int,
        rule_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[AutoModerationEdit],
    ) -> AutoModerationRule:
        return await self._call(
            AutoModerationRule,
            "PATCH",
            "/guilds/{guild_id}/auto-moderation/rules/{rule_id}",
            json=wire(options, AutoModerationEdit),
            reason=reason,
            guild_id=guild_id,
            rule_id=rule_id,
        )

    async def fetch(self, guild_id: int, rule_id: int, /) -> AutoModerationRule:
        return await self._call(
            AutoModerationRule,
            "GET",
            "/guilds/{guild_id}/auto-moderation/rules/{rule_id}",
            guild_id=guild_id,
            rule_id=rule_id,
        )

    async def list(self, guild_id: int, /) -> tuple[AutoModerationRule, ...]:
        return await self._call(
            each(AutoModerationRule),
            "GET",
            "/guilds/{guild_id}/auto-moderation/rules",
            guild_id=guild_id,
        )
