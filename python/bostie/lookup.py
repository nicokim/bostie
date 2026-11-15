from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from bostie.models import Channel, Guild, Member, Message, Role, User, WebhookMessage

if TYPE_CHECKING:
    from bostie.bot import Bot


class Lookup:
    """`bot.lookup`: an object from the cache, or from REST without caching it."""

    def __init__(self, bot: "Bot") -> None:
        self._bot = bot

    async def user(self, user_id: int, /) -> User:
        return await _either(
            self._bot.cache.user(user_id), lambda: self._bot.rest.users.fetch(user_id)
        )

    async def member(self, guild_id: int, user_id: int, /) -> Member:
        return await _either(
            self._bot.cache.member(guild_id, user_id),
            lambda: self._bot.rest.members.fetch(guild_id, user_id),
        )

    async def channel(self, channel_id: int, /) -> Channel:
        return await _either(
            self._bot.cache.channel(channel_id),
            lambda: self._bot.rest.channels.fetch(channel_id),
        )

    async def guild(self, guild_id: int, /) -> Guild:
        return await _either(
            self._bot.cache.guild(guild_id),
            lambda: self._bot.rest.guilds.fetch(guild_id),
        )

    async def role(self, guild_id: int, role_id: int, /) -> Role:
        return await _either(
            self._bot.cache.role(role_id),
            lambda: self._bot.rest.roles.fetch(guild_id, role_id),
        )

    async def message(
        self, channel_id: int, message_id: int, /
    ) -> Message | WebhookMessage:
        return await _either(
            self._bot.cache.message(message_id),
            lambda: self._bot.rest.messages.fetch(channel_id, message_id),
        )


async def _either[T](cached: T | None, fetch: Callable[[], Awaitable[T]]) -> T:
    return cached if cached is not None else await fetch()
