from datetime import datetime
from functools import partial
from typing import TypedDict, Unpack

from bostie.models import VoiceRegion, VoiceState, decode
from bostie.rest.base import Group, each, wire


class VoiceEditCurrentState(TypedDict, total=False):
    channel_id: int
    suppress: bool
    request_to_speak_timestamp: datetime | None


class VoiceEditState(TypedDict, total=False):
    channel_id: int
    suppress: bool


class Voice(Group):
    async def edit_current_state(
        self, guild_id: int, /, **options: Unpack[VoiceEditCurrentState]
    ) -> None:
        """Change the bot's suppress state or request to speak in a stage channel."""
        await self._http._request(
            "PATCH",
            "/guilds/{guild_id}/voice-states/@me",
            json=wire(options, VoiceEditCurrentState),
            guild_id=guild_id,
        )

    async def edit_state(
        self, guild_id: int, user_id: int, /, **options: Unpack[VoiceEditState]
    ) -> None:
        """Change a user's suppress state in a stage channel."""
        await self._http._request(
            "PATCH",
            "/guilds/{guild_id}/voice-states/{user_id}",
            json=wire(options, VoiceEditState),
            guild_id=guild_id,
            user_id=user_id,
        )

    async def fetch_current_state(self, guild_id: int, /) -> VoiceState:
        return await self._http._decoded(
            partial(decode, VoiceState),
            "GET",
            "/guilds/{guild_id}/voice-states/@me",
            guild_id=guild_id,
        )

    async def fetch_state(self, guild_id: int, user_id: int, /) -> VoiceState:
        return await self._http._decoded(
            partial(decode, VoiceState),
            "GET",
            "/guilds/{guild_id}/voice-states/{user_id}",
            guild_id=guild_id,
            user_id=user_id,
        )

    async def list_guild_regions(self, guild_id: int, /) -> tuple[VoiceRegion, ...]:
        """The guild's voice regions, VIP ones included when the guild has them."""
        return await self._http._decoded(
            each(partial(decode, VoiceRegion)),
            "GET",
            "/guilds/{guild_id}/regions",
            guild_id=guild_id,
        )

    async def list_regions(self) -> tuple[VoiceRegion, ...]:
        return await self._http._decoded(
            each(partial(decode, VoiceRegion)), "GET", "/voice/regions"
        )
