from collections.abc import Sequence
from typing import Required, TypedDict, Unpack

import msgspec

from bostie.enums import InviteTargetType
from bostie.file import File
from bostie.models import (
    Invite,
    InviteWithMetadata,
    TargetUsersJob,
    decode_invite,
)
from bostie.rest.base import ResourceGroup, each, wire


class InviteCreate(TypedDict, total=False):
    """`target_users_file` is a CSV of user ids, one per line under `user_id`."""

    max_age: int
    max_uses: int
    temporary: bool
    unique: bool
    target_type: InviteTargetType
    target_user_id: int
    target_application_id: int
    target_users_file: File
    target_user_ids: Sequence[int]
    role_ids: Sequence[int]


class InviteFetch(TypedDict, total=False):
    with_counts: bool
    guild_scheduled_event_id: int


class InviteBulkAddTargetUsers(TypedDict, total=False):
    user_ids: Required[Sequence[int]]


class InviteBulkDeleteTargetUsers(TypedDict, total=False):
    user_ids: Required[Sequence[int]]


class InviteUpdateTargetUsers(TypedDict, total=False):
    target_users_file: Required[File]


class Invites(ResourceGroup):
    async def add_target_user(self, code: str, user_id: int, /) -> None:
        await self._http.send(
            "PUT",
            "/invites/{code}/target-users/{user_id}",
            code=code,
            user_id=user_id,
        )

    async def bulk_add_target_users(
        self, code: str, /, **options: Unpack[InviteBulkAddTargetUsers]
    ) -> None:
        await self._http.send(
            "POST",
            "/invites/{code}/target-users/bulk-add",
            json=wire(options, InviteBulkAddTargetUsers),
            code=code,
        )

    async def bulk_delete_target_users(
        self, code: str, /, **options: Unpack[InviteBulkDeleteTargetUsers]
    ) -> None:
        await self._http.send(
            "POST",
            "/invites/{code}/target-users/bulk-delete",
            json=wire(options, InviteBulkDeleteTargetUsers),
            code=code,
        )

    async def create(
        self,
        channel_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[InviteCreate],
    ) -> Invite | InviteWithMetadata:
        file = options.pop("target_users_file", None)
        return await self._call(
            decode_invite,
            "POST",
            "/channels/{channel_id}/invites",
            json=wire(options, InviteCreate),
            form=None if file is None else {"target_users_file": file},
            reason=reason,
            channel_id=channel_id,
        )

    async def delete(self, code: str, /, *, reason: str | None = None) -> Invite:
        return await self._call(
            Invite,
            "DELETE",
            "/invites/{code}",
            reason=reason,
            code=code,
        )

    async def fetch(self, code: str, /, **options: Unpack[InviteFetch]) -> Invite:
        return await self._call(
            Invite,
            "GET",
            "/invites/{code}",
            query=wire(options, InviteFetch),
            code=code,
        )

    async def fetch_target_users_job_status(self, code: str, /) -> TargetUsersJob:
        return await self._call(
            TargetUsersJob,
            "GET",
            "/invites/{code}/target-users/job-status",
            code=code,
        )

    async def list(self, channel_id: int, /) -> tuple[InviteWithMetadata, ...]:
        return await self._call(
            each(InviteWithMetadata),
            "GET",
            "/channels/{channel_id}/invites",
            channel_id=channel_id,
        )

    async def list_guild(
        self, guild_id: int, /
    ) -> tuple[Invite | InviteWithMetadata, ...]:
        """The guild's invites, with their metadata if the bot can manage the guild."""
        return await self._call(
            each(decode_invite),
            "GET",
            "/guilds/{guild_id}/invites",
            guild_id=guild_id,
        )

    async def list_target_users(self, code: str, /) -> tuple[int, ...]:
        """The ids of the users who may see and accept the invite."""
        return await self._call(
            _user_ids, "GET", "/invites/{code}/target-users", code=code
        )

    async def remove_target_user(self, code: str, user_id: int, /) -> None:
        await self._http.send(
            "DELETE",
            "/invites/{code}/target-users/{user_id}",
            code=code,
            user_id=user_id,
        )

    async def update_target_users(
        self, code: str, /, **options: Unpack[InviteUpdateTargetUsers]
    ) -> None:
        """Replace who may accept the invite with the users of a CSV file."""
        fields = dict(options)
        file = fields.pop("target_users_file")
        await self._http.send(
            "PUT",
            "/invites/{code}/target-users",
            form=wire(fields, InviteUpdateTargetUsers) | {"target_users_file": file},
            code=code,
        )


def _user_ids(data: bytes) -> tuple[int, ...]:
    header, *ids = data.split() or [b""]
    if header != b"user_id" or not all(id.isdigit() for id in ids):
        raise msgspec.DecodeError("expected a CSV of user ids under a user_id header")
    return tuple(map(int, ids))
