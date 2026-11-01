from functools import partial

from bostie.models import User, decode
from bostie.rest.base import Group


class Users(Group):
    async def fetch(self, user_id: int, /) -> User:
        return await self._http._decoded(
            partial(decode, User), "GET", "/users/{user_id}", user_id=user_id
        )
