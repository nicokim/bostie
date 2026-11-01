from functools import partial

from bostie.models import GatewayBotInfo, GatewayInfo, decode
from bostie.rest.base import Group


class Gateway(Group):
    async def fetch(self) -> GatewayInfo:
        return await self._http._decoded(
            partial(decode, GatewayInfo), "GET", "/gateway"
        )

    async def fetch_bot(self) -> GatewayBotInfo:
        return await self._http._decoded(
            partial(decode, GatewayBotInfo), "GET", "/gateway/bot"
        )
