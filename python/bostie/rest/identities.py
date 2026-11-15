from typing import TypedDict, Unpack

from bostie._util import decode_answer
from bostie.models import (
    ApplicationIdentity,
    ApplicationIdentityProfile,
    ProfileData,
)
from bostie.rest._base import ResourceGroup, decoding, each, wire


class IdentityUpdateProfile(TypedDict, total=False):
    username: str
    data: ProfileData


class Identities(ResourceGroup):
    async def delete(
        self,
        user_id: int,
        application_id: int,
        provider_type: str,
        provider_issued_user_id: str,
        /,
        *,
        provider_id: str | None = None,
    ) -> None:
        """Delete the identity and its profile, unless it is the last linked one."""
        await self._http.send(
            "POST",
            "/users/{user_id}/application-identities/{application_id}/{provider_type}/{provider_issued_user_id}/delete",
            json=None if provider_id is None else {"provider_id": provider_id},
            user_id=user_id,
            application_id=application_id,
            provider_type=provider_type,
            provider_issued_user_id=provider_issued_user_id,
        )

    async def fetch_profile(
        self, application_id: int, user_id: int, provider_issued_user_id: str, /
    ) -> ApplicationIdentityProfile:
        return await self._call(
            ApplicationIdentityProfile,
            "GET",
            "/applications/{application_id}/users/{user_id}/identities/{provider_issued_user_id}/profile",
            application_id=application_id,
            user_id=user_id,
            provider_issued_user_id=provider_issued_user_id,
        )

    async def list(
        self, user_id: int, application_id: int, /
    ) -> tuple[ApplicationIdentity, ...]:
        return await self._call(
            each(ApplicationIdentity, "identities"),
            "GET",
            "/users/{user_id}/application-identities/{application_id}",
            user_id=user_id,
            application_id=application_id,
        )

    async def list_by_external_id(
        self,
        application_id: int,
        provider_type: str,
        provider_issued_user_id: str,
        /,
        *,
        provider_id: str | None = None,
    ) -> tuple[ApplicationIdentity, ...]:
        """The identities of the user the external account belongs to, if any."""
        return await self._call(
            each(ApplicationIdentity, "identities"),
            "GET",
            "/applications/{application_id}/application-identities/{provider_type}/{provider_issued_user_id}",
            query={"provider_id": provider_id},
            application_id=application_id,
            provider_type=provider_type,
            provider_issued_user_id=provider_issued_user_id,
        )

    async def update_profile(
        self,
        application_id: int,
        user_id: int,
        provider_issued_user_id: str,
        /,
        **options: Unpack[IdentityUpdateProfile],
    ) -> ApplicationIdentityProfile | None:
        """Replace the profile with `data`; only the first write returns it."""
        answer = await self._http.send(
            "PATCH",
            "/applications/{application_id}/users/{user_id}/identities/{provider_issued_user_id}/profile",
            json=wire(options, IdentityUpdateProfile),
            application_id=application_id,
            user_id=user_id,
            provider_issued_user_id=provider_issued_user_id,
        )
        if not answer:
            return None
        return decode_answer(
            decoding(ApplicationIdentityProfile), answer, took_effect=True
        )
