from collections.abc import Mapping
from typing import TypedDict, Unpack

from bostie.enums import Permissions
from bostie.file import File
from bostie.models import Role, RoleColors
from bostie.models.base import decoder
from bostie.rest._base import ResourceGroup, each, wire
from bostie.rest.members import _decode_permissions


class RoleCreate(TypedDict, total=False):
    name: str
    permissions: Permissions
    colors: RoleColors
    hoist: bool
    icon: File | None
    unicode_emoji: str | None
    mentionable: bool


class RoleEdit(TypedDict, total=False):
    name: str | None
    permissions: Permissions | None
    colors: RoleColors | None
    hoist: bool | None
    icon: File | None
    unicode_emoji: str | None
    mentionable: bool | None


def _member_counts(data: bytes) -> dict[int, int]:
    counts: dict[int, int] = decoder(dict[int, int]).decode(data)
    return counts


class Roles(ResourceGroup):
    async def create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[RoleCreate],
    ) -> Role:
        return await self._call(
            Role,
            "POST",
            "/guilds/{guild_id}/roles",
            json=wire(options, RoleCreate),
            reason=reason,
            guild_id=guild_id,
        )

    async def delete(
        self, guild_id: int, role_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http.send(
            "DELETE",
            "/guilds/{guild_id}/roles/{role_id}",
            reason=reason,
            guild_id=guild_id,
            role_id=role_id,
        )

    async def edit(
        self,
        guild_id: int,
        role_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[RoleEdit],
    ) -> Role:
        return await self._call(
            Role,
            "PATCH",
            "/guilds/{guild_id}/roles/{role_id}",
            json=wire(options, RoleEdit),
            reason=reason,
            guild_id=guild_id,
            role_id=role_id,
        )

    async def edit_positions(
        self,
        guild_id: int,
        /,
        positions: Mapping[int, int | None],
        *,
        reason: str | None = None,
    ) -> tuple[Role, ...]:
        """Move roles, given as role id to position; returns every role of the guild."""
        return await self._call(
            each(Role),
            "PATCH",
            "/guilds/{guild_id}/roles",
            json=[
                {"id": id, "position": position} for id, position in positions.items()
            ],
            reason=reason,
            guild_id=guild_id,
        )

    async def fetch(self, guild_id: int, role_id: int, /) -> Role:
        return await self._call(
            Role,
            "GET",
            "/guilds/{guild_id}/roles/{role_id}",
            guild_id=guild_id,
            role_id=role_id,
        )

    async def fetch_member_counts(self, guild_id: int, /) -> dict[int, int]:
        """How many members hold each role, by role id, `@everyone` left out."""
        return await self._call(
            _member_counts,
            "GET",
            "/guilds/{guild_id}/roles/member-counts",
            guild_id=guild_id,
        )

    async def fetch_permissions(self, guild_id: int, role_id: int, /) -> Permissions:
        """The role's guild permissions combined with those of `@everyone`."""
        return await self._call(
            _decode_permissions,
            "GET",
            "/guilds/{guild_id}/permissions/role/{role_id}",
            guild_id=guild_id,
            role_id=role_id,
        )

    async def list(self, guild_id: int, /) -> tuple[Role, ...]:
        return await self._call(
            each(Role),
            "GET",
            "/guilds/{guild_id}/roles",
            guild_id=guild_id,
        )
