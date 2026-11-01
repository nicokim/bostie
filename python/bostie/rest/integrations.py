from functools import partial

from bostie.models import Integration, decode
from bostie.rest.base import Group, each


class Integrations(Group):
    async def delete(
        self, guild_id: int, integration_id: int, /, *, reason: str | None = None
    ) -> None:
        """Remove an integration, with its webhooks and its bot."""
        await self._http._request(
            "DELETE",
            "/guilds/{guild_id}/integrations/{integration_id}",
            reason=reason,
            guild_id=guild_id,
            integration_id=integration_id,
        )

    async def list(self, guild_id: int, /) -> tuple[Integration, ...]:
        """The guild's integrations, at most the 50 Discord returns."""
        return await self._http._decoded(
            each(partial(decode, Integration)),
            "GET",
            "/guilds/{guild_id}/integrations",
            guild_id=guild_id,
        )
