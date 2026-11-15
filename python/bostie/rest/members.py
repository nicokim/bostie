from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Required, TypedDict, Unpack

import msgspec

from bostie.enums import MemberFlags, Permissions
from bostie.file import File
from bostie.http import decode_answer
from bostie.models import Member
from bostie.models.base import decoder
from bostie.rest.base import ResourceGroup, decoding, each, wire


class MemberAdd(TypedDict, total=False):
    access_token: Required[str]
    nick: str
    roles: Sequence[int]
    mute: bool
    deaf: bool


class MemberEdit(TypedDict, total=False):
    nick: str | None
    roles: Sequence[int] | None
    mute: bool | None
    deaf: bool | None
    channel_id: int | None
    communication_disabled_until: datetime | None
    flags: MemberFlags | None


class MemberEditCurrent(TypedDict, total=False):
    nick: str | None
    banner: File | None
    avatar: File | None
    bio: str | None


class MemberList(TypedDict, total=False):
    after: int
    limit: int


class MemberSearch(TypedDict, total=False):
    query: Required[str]
    limit: int


class _Permissions(msgspec.Struct):
    permissions: str


def decode_permissions(data: bytes) -> Permissions:
    found = decoder(_Permissions).decode(data)
    return Permissions(int(found.permissions))


class Members(ResourceGroup):
    async def add(
        self, guild_id: int, user_id: int, /, **options: Unpack[MemberAdd]
    ) -> Member | None:
        """Add a user with their OAuth2 token; None if they were already a member."""
        data = await self._http.send(
            "PUT",
            "/guilds/{guild_id}/members/{user_id}",
            json=wire(options, MemberAdd),
            guild_id=guild_id,
            user_id=user_id,
        )
        if not data:
            return None
        return decode_answer(decoding(Member), data, took_effect=True)

    async def add_role(
        self,
        guild_id: int,
        user_id: int,
        role_id: int,
        /,
        *,
        reason: str | None = None,
    ) -> None:
        await self._http.send(
            "PUT",
            "/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
            reason=reason,
            guild_id=guild_id,
            user_id=user_id,
            role_id=role_id,
        )

    async def edit(
        self,
        guild_id: int,
        user_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[MemberEdit],
    ) -> Member:
        return await self._call(
            Member,
            "PATCH",
            "/guilds/{guild_id}/members/{user_id}",
            json=wire(options, MemberEdit),
            reason=reason,
            guild_id=guild_id,
            user_id=user_id,
        )

    async def edit_current(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[MemberEditCurrent],
    ) -> Member:
        return await self._call(
            Member,
            "PATCH",
            "/guilds/{guild_id}/members/@me",
            json=wire(options, MemberEditCurrent),
            reason=reason,
            guild_id=guild_id,
        )

    async def fetch(self, guild_id: int, user_id: int, /) -> Member:
        return await self._call(
            Member,
            "GET",
            "/guilds/{guild_id}/members/{user_id}",
            guild_id=guild_id,
            user_id=user_id,
        )

    async def fetch_permissions(self, guild_id: int, user_id: int, /) -> Permissions:
        """The member's guild permissions from their roles and `@everyone`."""
        return await self._call(
            decode_permissions,
            "GET",
            "/guilds/{guild_id}/permissions/member/{user_id}",
            guild_id=guild_id,
            user_id=user_id,
        )

    def list(
        self, guild_id: int, /, **options: Unpack[MemberList]
    ) -> AsyncIterator[Member]:
        """Walk the guild's members by user id; needs the Server Members intent."""
        pages = self._paginate(
            "/guilds/{guild_id}/members",
            wire(options, MemberList),
            decodes=each(Member),
            items=tuple,
            cursor=lambda member: member.id,
            page_size=1000,
            forward=True,
            guild_id=guild_id,
        )
        return (member async for page in pages for member in page)

    async def remove(
        self, guild_id: int, user_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http.send(
            "DELETE",
            "/guilds/{guild_id}/members/{user_id}",
            reason=reason,
            guild_id=guild_id,
            user_id=user_id,
        )

    async def remove_role(
        self,
        guild_id: int,
        user_id: int,
        role_id: int,
        /,
        *,
        reason: str | None = None,
    ) -> None:
        await self._http.send(
            "DELETE",
            "/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
            reason=reason,
            guild_id=guild_id,
            user_id=user_id,
            role_id=role_id,
        )

    async def search(
        self, guild_id: int, /, **options: Unpack[MemberSearch]
    ) -> tuple[Member, ...]:
        """Members whose username or nickname starts with `query`."""
        return await self._call(
            each(Member),
            "GET",
            "/guilds/{guild_id}/members/search",
            query=wire(options, MemberSearch),
            guild_id=guild_id,
        )


class BearerMembers(ResourceGroup):
    async def fetch_current(self, guild_id: int, /) -> Member:
        return await self._call(
            Member,
            "GET",
            "/users/@me/guilds/{guild_id}/member",
            guild_id=guild_id,
        )
