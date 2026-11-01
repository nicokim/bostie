from collections.abc import Sequence
from functools import partial
from typing import Required, TypedDict, Unpack

from bostie.enums import AutoModerationEventType, AutoModerationTriggerType
from bostie.models import (
    AutoModerationAction,
    AutoModerationRule,
    AutoModerationTriggerMetadata,
    decode,
)
from bostie.rest.base import Group, each, wire


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


class AutoModeration(Group):
    async def create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[AutoModerationCreate],
    ) -> AutoModerationRule:
        return await self._http._decoded(
            partial(decode, AutoModerationRule),
            "POST",
            "/guilds/{guild_id}/auto-moderation/rules",
            json=wire(options, AutoModerationCreate),
            reason=reason,
            guild_id=guild_id,
        )

    async def delete(
        self, guild_id: int, rule_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http._request(
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
        return await self._http._decoded(
            partial(decode, AutoModerationRule),
            "PATCH",
            "/guilds/{guild_id}/auto-moderation/rules/{rule_id}",
            json=wire(options, AutoModerationEdit),
            reason=reason,
            guild_id=guild_id,
            rule_id=rule_id,
        )

    async def fetch(self, guild_id: int, rule_id: int, /) -> AutoModerationRule:
        return await self._http._decoded(
            partial(decode, AutoModerationRule),
            "GET",
            "/guilds/{guild_id}/auto-moderation/rules/{rule_id}",
            guild_id=guild_id,
            rule_id=rule_id,
        )

    async def list(self, guild_id: int, /) -> tuple[AutoModerationRule, ...]:
        return await self._http._decoded(
            each(partial(decode, AutoModerationRule)),
            "GET",
            "/guilds/{guild_id}/auto-moderation/rules",
            guild_id=guild_id,
        )
