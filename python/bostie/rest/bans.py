from collections.abc import AsyncIterator, Sequence
from typing import Required, TypedDict, Unpack

from bostie.models import Ban, BulkBan
from bostie.rest._base import ResourceGroup, each, wire


class BanBulkCreate(TypedDict, total=False):
    user_ids: Required[Sequence[int]]
    delete_message_seconds: int


class BanCreate(TypedDict, total=False):
    delete_message_seconds: int


class BanList(TypedDict, total=False):
    before: int
    after: int
    limit: int


class Bans(ResourceGroup):
    async def bulk_create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[BanBulkCreate],
    ) -> BulkBan:
        return await self._call(
            BulkBan,
            "POST",
            "/guilds/{guild_id}/bulk-ban",
            json=wire(options, BanBulkCreate),
            reason=reason,
            guild_id=guild_id,
        )

    async def create(
        self,
        guild_id: int,
        user_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[BanCreate],
    ) -> None:
        await self._http.send(
            "PUT",
            "/guilds/{guild_id}/bans/{user_id}",
            json=wire(options, BanCreate),
            reason=reason,
            guild_id=guild_id,
            user_id=user_id,
        )

    async def fetch(self, guild_id: int, user_id: int, /) -> Ban:
        return await self._call(
            Ban,
            "GET",
            "/guilds/{guild_id}/bans/{user_id}",
            guild_id=guild_id,
            user_id=user_id,
        )

    def list(self, guild_id: int, /, **options: Unpack[BanList]) -> AsyncIterator[Ban]:
        """Walk the guild's bans by user id, lowest first unless `before` is given."""
        pages = self._paginate(
            "/guilds/{guild_id}/bans",
            wire(options, BanList),
            decodes=each(Ban),
            items=tuple,
            cursor=lambda ban: ban.user.id,
            page_size=1000,
            forward=True,
            guild_id=guild_id,
        )
        return (ban async for page in pages for ban in page)

    async def remove(
        self, guild_id: int, user_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http.send(
            "DELETE",
            "/guilds/{guild_id}/bans/{user_id}",
            reason=reason,
            guild_id=guild_id,
            user_id=user_id,
        )
