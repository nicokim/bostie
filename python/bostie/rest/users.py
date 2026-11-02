from functools import partial
from typing import TypedDict, Unpack

from bostie.file import File
from bostie.models import CurrentUser, User, decode
from bostie.rest.base import Group, wire


class UserEditCurrent(TypedDict, total=False):
    username: str
    avatar: File | None
    banner: File | None


class Users(Group):
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

    async def fetch_current(self) -> CurrentUser:
        return await self._http._decoded(
            partial(decode, CurrentUser), "GET", "/users/@me"
        )
