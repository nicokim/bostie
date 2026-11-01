from functools import partial

from bostie.models import Guild, decode
from bostie.rest.base import Group


class Guilds(Group):
    async def fetch(self, guild_id: int, /) -> Guild:
        return await self._http._decoded(
            partial(decode, Guild), "GET", "/guilds/{guild_id}", guild_id=guild_id
        )
