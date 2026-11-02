from collections.abc import Mapping
from functools import partial
from typing import TypedDict, Unpack

from bostie.file import File
from bostie.models import (
    ApplicationRoleConnection,
    Connection,
    CurrentUser,
    User,
    decode,
)
from bostie.rest.base import Group, each, wire


class UserEditCurrent(TypedDict, total=False):
    username: str
    avatar: File | None
    banner: File | None


class _CurrentUser(Group):
    async def fetch_current(self) -> CurrentUser:
        return await self._http._decoded(
            partial(decode, CurrentUser), "GET", "/users/@me"
        )


class UserUpdateRoleConnection(TypedDict, total=False):
    platform_name: str
    platform_username: str
    metadata: Mapping[str, str]


class Users(_CurrentUser):
    async def edit_current(self, **options: Unpack[UserEditCurrent]) -> CurrentUser:
        return await self._http._decoded(
            partial(decode, CurrentUser),
            "PATCH",
            "/users/@me",
            json=wire(options, UserEditCurrent),
        )

    async def fetch(self, user_id: int, /) -> User:
        return await self._http._decoded(
            partial(decode, User), "GET", "/users/{user_id}", user_id=user_id
        )


class BearerUsers(_CurrentUser):
    async def delete_role_connection(self, application_id: int, /) -> None:
        await self._http._request(
            "DELETE",
            "/users/@me/applications/{application_id}/role-connection",
            application_id=application_id,
        )

    async def fetch_role_connection(
        self, application_id: int, /
    ) -> ApplicationRoleConnection:
        return await self._http._decoded(
            partial(decode, ApplicationRoleConnection),
            "GET",
            "/users/@me/applications/{application_id}/role-connection",
            application_id=application_id,
        )

    async def list_connections(self) -> tuple[Connection, ...]:
        return await self._http._decoded(
            each(partial(decode, Connection)), "GET", "/users/@me/connections"
        )

    async def update_role_connection(
        self, application_id: int, /, **options: Unpack[UserUpdateRoleConnection]
    ) -> ApplicationRoleConnection:
        """Set the user's linked role values; `metadata` maps each key to a string."""
        return await self._http._decoded(
            partial(decode, ApplicationRoleConnection),
            "PUT",
            "/users/@me/applications/{application_id}/role-connection",
            json=wire(options, UserUpdateRoleConnection),
            application_id=application_id,
        )
