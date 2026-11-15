from bostie.models import GatewayBotInfo, GatewayInfo
from bostie.rest.base import ResourceGroup


class Gateway(ResourceGroup):
    async def fetch(self) -> GatewayInfo:
        return await self._call(GatewayInfo, "GET", "/gateway")

    async def fetch_bot(self) -> GatewayBotInfo:
        return await self._call(GatewayBotInfo, "GET", "/gateway/bot")
