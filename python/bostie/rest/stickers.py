from functools import partial
from typing import Required, TypedDict, Unpack

from bostie.file import File
from bostie.models import Sticker, StickerPack, decode
from bostie.models.base import check_keys
from bostie.rest.base import Group, each, wire


class StickerCreate(TypedDict, total=False):
    name: Required[str]
    description: str
    tags: Required[str]
    file: Required[File]


class StickerEdit(TypedDict, total=False):
    name: str
    description: str | None
    tags: str


class Stickers(Group):
    async def create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[StickerCreate],
    ) -> Sticker:
        """Upload a PNG, APNG, GIF or Lottie JSON `file` as a guild sticker."""
        keys = StickerCreate.__required_keys__ | StickerCreate.__optional_keys__
        check_keys(options, keys, "options")
        return await self._http._decoded(
            partial(decode, Sticker),
            "POST",
            "/guilds/{guild_id}/stickers",
            form=options,
            reason=reason,
            guild_id=guild_id,
        )

    async def delete(
        self, guild_id: int, sticker_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http._request(
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
        return await self._http._decoded(
            partial(decode, Sticker),
            "PATCH",
            "/guilds/{guild_id}/stickers/{sticker_id}",
            json=wire(options, StickerEdit),
            reason=reason,
            guild_id=guild_id,
            sticker_id=sticker_id,
        )

    async def fetch(self, sticker_id: int, /) -> Sticker:
        return await self._http._decoded(
            partial(decode, Sticker),
            "GET",
            "/stickers/{sticker_id}",
            sticker_id=sticker_id,
        )

    async def fetch_guild(self, guild_id: int, sticker_id: int, /) -> Sticker:
        return await self._http._decoded(
            partial(decode, Sticker),
            "GET",
            "/guilds/{guild_id}/stickers/{sticker_id}",
            guild_id=guild_id,
            sticker_id=sticker_id,
        )

    async def fetch_pack(self, pack_id: int, /) -> StickerPack:
        return await self._http._decoded(
            partial(decode, StickerPack),
            "GET",
            "/sticker-packs/{pack_id}",
            pack_id=pack_id,
        )

    async def list(self, guild_id: int, /) -> tuple[Sticker, ...]:
        return await self._http._decoded(
            each(partial(decode, Sticker)),
            "GET",
            "/guilds/{guild_id}/stickers",
            guild_id=guild_id,
        )

    async def list_packs(self) -> tuple[StickerPack, ...]:
        return await self._http._decoded(
            each(partial(decode, StickerPack), "sticker_packs"),
            "GET",
            "/sticker-packs",
        )
