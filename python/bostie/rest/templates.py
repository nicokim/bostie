from functools import partial
from typing import Required, TypedDict, Unpack

from bostie.models import GuildTemplate, decode
from bostie.rest.base import Group, each, wire


class TemplateCreate(TypedDict, total=False):
    name: Required[str]
    description: str | None


class TemplateEdit(TypedDict, total=False):
    name: str
    description: str | None


class Templates(Group):
    async def create(
        self, guild_id: int, /, **options: Unpack[TemplateCreate]
    ) -> GuildTemplate:
        return await self._http._decoded(
            partial(decode, GuildTemplate),
            "POST",
            "/guilds/{guild_id}/templates",
            json=wire(options, TemplateCreate),
            guild_id=guild_id,
        )

    async def delete(self, guild_id: int, code: str, /) -> GuildTemplate:
        return await self._http._decoded(
            partial(decode, GuildTemplate),
            "DELETE",
            "/guilds/{guild_id}/templates/{code}",
            guild_id=guild_id,
            code=code,
        )

    async def edit(
        self, guild_id: int, code: str, /, **options: Unpack[TemplateEdit]
    ) -> GuildTemplate:
        return await self._http._decoded(
            partial(decode, GuildTemplate),
            "PATCH",
            "/guilds/{guild_id}/templates/{code}",
            json=wire(options, TemplateEdit),
            guild_id=guild_id,
            code=code,
        )

    async def fetch(self, code: str, /) -> GuildTemplate:
        return await self._http._decoded(
            partial(decode, GuildTemplate),
            "GET",
            "/guilds/templates/{code}",
            code=code,
        )

    async def list(self, guild_id: int, /) -> tuple[GuildTemplate, ...]:
        return await self._http._decoded(
            each(partial(decode, GuildTemplate)),
            "GET",
            "/guilds/{guild_id}/templates",
            guild_id=guild_id,
        )

    async def sync(self, guild_id: int, code: str, /) -> GuildTemplate:
        """Update the template to the guild's current state."""
        return await self._http._decoded(
            partial(decode, GuildTemplate),
            "PUT",
            "/guilds/{guild_id}/templates/{code}",
            guild_id=guild_id,
            code=code,
        )
