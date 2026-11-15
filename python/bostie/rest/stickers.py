from typing import Required, TypedDict, Unpack

from bostie.file import File
from bostie.models import Sticker, StickerPack
from bostie.rest._base import ResourceGroup, each, wire


class StickerCreate(TypedDict, total=False):
    name: Required[str]
    description: str
    tags: Required[str]
    file: Required[File]


class StickerEdit(TypedDict, total=False):
    name: str
    description: str | None
    tags: str


class Stickers(ResourceGroup):
    async def create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[StickerCreate],
    ) -> Sticker:
        """Upload a PNG, APNG, GIF or Lottie JSON `file` as a guild sticker."""
        fields = dict(options)
        file = fields.pop("file")
        return await self._call(
            Sticker,
            "POST",
            "/guilds/{guild_id}/stickers",
            form=wire(fields, StickerCreate) | {"file": file},
            reason=reason,
            guild_id=guild_id,
        )

    async def delete(
        self, guild_id: int, sticker_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http.send(
            "DELETE",
            "/guilds/{guild_id}/stickers/{sticker_id}",
            reason=reason,
            guild_id=guild_id,
            sticker_id=sticker_id,
        )

    async def edit(
        self,
        guild_id: int,
        sticker_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[StickerEdit],
    ) -> Sticker:
        return await self._call(
            Sticker,
            "PATCH",
            "/guilds/{guild_id}/stickers/{sticker_id}",
            json=wire(options, StickerEdit),
            reason=reason,
            guild_id=guild_id,
            sticker_id=sticker_id,
        )

    async def fetch(self, sticker_id: int, /) -> Sticker:
        return await self._call(
            Sticker,
            "GET",
            "/stickers/{sticker_id}",
            sticker_id=sticker_id,
        )

    async def fetch_guild(self, guild_id: int, sticker_id: int, /) -> Sticker:
        return await self._call(
            Sticker,
            "GET",
            "/guilds/{guild_id}/stickers/{sticker_id}",
            guild_id=guild_id,
            sticker_id=sticker_id,
        )

    async def fetch_pack(self, pack_id: int, /) -> StickerPack:
        return await self._call(
            StickerPack,
            "GET",
            "/sticker-packs/{pack_id}",
            pack_id=pack_id,
        )

    async def list(self, guild_id: int, /) -> tuple[Sticker, ...]:
        return await self._call(
            each(Sticker),
            "GET",
            "/guilds/{guild_id}/stickers",
            guild_id=guild_id,
        )

    async def list_packs(self) -> tuple[StickerPack, ...]:
        return await self._call(
            each(StickerPack, "sticker_packs"),
            "GET",
            "/sticker-packs",
        )
