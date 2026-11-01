from functools import partial

from bostie.models import Channel, decode
from bostie.rest.base import Group


class Channels(Group):
    async def fetch(self, channel_id: int, /) -> Channel:
        return await self._http._decoded(
            partial(decode, Channel),
            "GET",
            "/channels/{channel_id}",
            channel_id=channel_id,
        )
