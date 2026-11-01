from functools import partial
from typing import Required, TypedDict, Unpack

from bostie.file import File
from bostie.models import SoundboardSound, decode
from bostie.rest.base import Group, each, wire


class SoundboardCreate(TypedDict, total=False):
    name: Required[str]
    sound: Required[File]
    volume: float | None
    emoji_id: int | None
    emoji_name: str | None


class SoundboardEdit(TypedDict, total=False):
    name: str
    volume: float | None
    emoji_id: int | None
    emoji_name: str | None


class SoundboardSend(TypedDict, total=False):
    sound_id: Required[int]
    source_guild_id: int


class Soundboard(Group):
    async def create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[SoundboardCreate],
    ) -> SoundboardSound:
        return await self._http._decoded(
            partial(decode, SoundboardSound),
            "POST",
            "/guilds/{guild_id}/soundboard-sounds",
            json=wire(options, SoundboardCreate),
            reason=reason,
            guild_id=guild_id,
        )

    async def delete(
        self, guild_id: int, sound_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http._request(
            "DELETE",
            "/guilds/{guild_id}/soundboard-sounds/{sound_id}",
            reason=reason,
            guild_id=guild_id,
            sound_id=sound_id,
        )

    async def edit(
        self,
        guild_id: int,
        sound_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[SoundboardEdit],
    ) -> SoundboardSound:
        return await self._http._decoded(
            partial(decode, SoundboardSound),
            "PATCH",
            "/guilds/{guild_id}/soundboard-sounds/{sound_id}",
            json=wire(options, SoundboardEdit),
            reason=reason,
            guild_id=guild_id,
            sound_id=sound_id,
        )

    async def fetch(self, guild_id: int, sound_id: int, /) -> SoundboardSound:
        return await self._http._decoded(
            partial(decode, SoundboardSound),
            "GET",
            "/guilds/{guild_id}/soundboard-sounds/{sound_id}",
            guild_id=guild_id,
            sound_id=sound_id,
        )

    async def list(self, guild_id: int, /) -> tuple[SoundboardSound, ...]:
        return await self._http._decoded(
            each(partial(decode, SoundboardSound), "items"),
            "GET",
            "/guilds/{guild_id}/soundboard-sounds",
            guild_id=guild_id,
        )

    async def list_default(self) -> tuple[SoundboardSound, ...]:
        return await self._http._decoded(
            each(partial(decode, SoundboardSound)), "GET", "/soundboard-default-sounds"
        )

    async def send(self, channel_id: int, /, **options: Unpack[SoundboardSend]) -> None:
        """Play a sound in the voice channel the bot is connected to."""
        await self._http._request(
            "POST",
            "/channels/{channel_id}/send-soundboard-sound",
            json=wire(options, SoundboardSend),
            channel_id=channel_id,
        )
