from collections.abc import Sequence
from typing import Required, TypedDict, Unpack

from bostie.file import File
from bostie.models import Emoji
from bostie.rest._base import ResourceGroup, each, wire


class EmojiCreate(TypedDict, total=False):
    name: Required[str]
    image: Required[File]
    roles: Sequence[int]


class EmojiEdit(TypedDict, total=False):
    name: str
    roles: Sequence[int] | None


class EmojiCreateApplication(TypedDict, total=False):
    name: Required[str]
    image: Required[File]


class EmojiEditApplication(TypedDict, total=False):
    name: str


class Emojis(ResourceGroup):
    async def create(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[EmojiCreate],
    ) -> Emoji:
        return await self._call(
            Emoji,
            "POST",
            "/guilds/{guild_id}/emojis",
            json=wire(options, EmojiCreate),
            reason=reason,
            guild_id=guild_id,
        )

    async def create_application(
        self, application_id: int, /, **options: Unpack[EmojiCreateApplication]
    ) -> Emoji:
        return await self._call(
            Emoji,
            "POST",
            "/applications/{application_id}/emojis",
            json=wire(options, EmojiCreateApplication),
            application_id=application_id,
        )

    async def delete(
        self, guild_id: int, emoji_id: int, /, *, reason: str | None = None
    ) -> None:
        await self._http.send(
            "DELETE",
            "/guilds/{guild_id}/emojis/{emoji_id}",
            reason=reason,
            guild_id=guild_id,
            emoji_id=emoji_id,
        )

    async def delete_application(self, application_id: int, emoji_id: int, /) -> None:
        await self._http.send(
            "DELETE",
            "/applications/{application_id}/emojis/{emoji_id}",
            application_id=application_id,
            emoji_id=emoji_id,
        )

    async def edit(
        self,
        guild_id: int,
        emoji_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[EmojiEdit],
    ) -> Emoji:
        return await self._call(
            Emoji,
            "PATCH",
            "/guilds/{guild_id}/emojis/{emoji_id}",
            json=wire(options, EmojiEdit),
            reason=reason,
            guild_id=guild_id,
            emoji_id=emoji_id,
        )

    async def edit_application(
        self,
        application_id: int,
        emoji_id: int,
        /,
        **options: Unpack[EmojiEditApplication],
    ) -> Emoji:
        return await self._call(
            Emoji,
            "PATCH",
            "/applications/{application_id}/emojis/{emoji_id}",
            json=wire(options, EmojiEditApplication),
            application_id=application_id,
            emoji_id=emoji_id,
        )

    async def fetch(self, guild_id: int, emoji_id: int, /) -> Emoji:
        return await self._call(
            Emoji,
            "GET",
            "/guilds/{guild_id}/emojis/{emoji_id}",
            guild_id=guild_id,
            emoji_id=emoji_id,
        )

    async def fetch_application(self, application_id: int, emoji_id: int, /) -> Emoji:
        return await self._call(
            Emoji,
            "GET",
            "/applications/{application_id}/emojis/{emoji_id}",
            application_id=application_id,
            emoji_id=emoji_id,
        )

    async def list(self, guild_id: int, /) -> tuple[Emoji, ...]:
        return await self._call(
            each(Emoji),
            "GET",
            "/guilds/{guild_id}/emojis",
            guild_id=guild_id,
        )

    async def list_application(self, application_id: int, /) -> tuple[Emoji, ...]:
        return await self._call(
            each(Emoji, "items"),
            "GET",
            "/applications/{application_id}/emojis",
            application_id=application_id,
        )
