from collections.abc import Mapping

from bostie.models import AccessToken
from bostie.rest.base import ResourceGroup


class PartnerSDK(ResourceGroup):
    async def create_provisional_token(
        self, *, external_user_id: str, preferred_global_name: str | None = None
    ) -> AccessToken:
        """A Social SDK token for a player of the game's own account system."""
        name = (
            {}
            if preferred_global_name is None
            else {"preferred_global_name": preferred_global_name}
        )
        return await self._call(
            AccessToken,
            "POST",
            "/partner-sdk/token/bot",
            json={"external_user_id": external_user_id} | name,
        )

    async def exchange_child_token(
        self,
        *,
        parent_access_token: str,
        child_application_id: int,
        parent_client_secret: str,
    ) -> AccessToken:
        """A child game's token for a publisher's access token."""
        return await self._call(
            AccessToken,
            "POST",
            "/partner-sdk/child-token",
            json={
                "parent_access_token": parent_access_token,
                "child_application_id": child_application_id,
                "parent_client_secret": parent_client_secret,
            },
        )

    async def exchange_external_token(
        self,
        *,
        client_id: int,
        client_secret: str,
        external_auth_type: str,
        external_auth_token: str,
    ) -> AccessToken:
        """A Social SDK token for a token of an identity provider the app set up."""
        return await self._call(
            AccessToken,
            "POST",
            "/partner-sdk/token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "external_auth_type": external_auth_type,
                "external_auth_token": external_auth_token,
            },
        )

    async def unmerge_external_account(
        self,
        *,
        client_id: int,
        client_secret: str,
        external_auth_type: str,
        external_auth_token: str,
    ) -> None:
        """Split the Discord account from the provisional one of an identity token."""
        await self._http.send(
            "POST",
            "/partner-sdk/provisional-accounts/unmerge",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "external_auth_type": external_auth_type,
                "external_auth_token": external_auth_token,
            },
        )

    async def unmerge_provisional_account(self, *, external_user_id: str) -> None:
        """Split the Discord account from the provisional account of a player."""
        await self._http.send(
            "POST",
            "/partner-sdk/provisional-accounts/unmerge/bot",
            json={"external_user_id": external_user_id},
        )

    async def update_dm_moderation_metadata(
        self,
        user_id_1: int,
        user_id_2: int,
        message_id: int,
        /,
        metadata: Mapping[str, str],
    ) -> None:
        """Tell the game clients what to do with a direct message between two users."""
        await self._http.send(
            "PUT",
            "/partner-sdk/dms/{user_id_1}/{user_id_2}/messages/{message_id}/moderation-metadata",
            json=metadata,
            user_id_1=user_id_1,
            user_id_2=user_id_2,
            message_id=message_id,
        )
