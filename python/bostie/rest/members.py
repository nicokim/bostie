from functools import partial

from bostie.models import Member, decode
from bostie.rest.base import Group


class Members(Group):
    async def fetch(self, guild_id: int, user_id: int, /) -> Member:
        return await self._http._decoded(
            partial(decode, Member),
            "GET",
            "/guilds/{guild_id}/members/{user_id}",
            guild_id=guild_id,
            user_id=user_id,
        )
