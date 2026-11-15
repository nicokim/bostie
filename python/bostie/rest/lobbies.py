from collections.abc import Mapping, Sequence
from typing import Required, TypedDict, Unpack

import msgspec

from bostie.enums import LobbyMemberFlags, MessageFlags
from bostie.models import Lobby, LobbyMember, LobbyMessage
from bostie.models.base import decoder
from bostie.rest.base import ResourceGroup, each, wire


class LobbyMemberAdd(TypedDict, total=False):
    metadata: Mapping[str, str] | None
    flags: LobbyMemberFlags
    additional_name: str | None


class LobbyMemberSet(LobbyMemberAdd, total=False):
    id: Required[int]


class LobbyMemberUpdate(LobbyMemberSet, total=False):
    remove_member: bool


class LobbyCreate(TypedDict, total=False):
    metadata: Mapping[str, str] | None
    members: Sequence[LobbyMemberSet]
    idle_timeout_seconds: int


class LobbyCreateOrJoin(TypedDict, total=False):
    secret: Required[str]
    idle_timeout_seconds: int
    lobby_metadata: Mapping[str, str] | None
    member_metadata: Mapping[str, str] | None


class LobbyListMessages(TypedDict, total=False):
    limit: int


class LobbySendMessage(TypedDict, total=False):
    metadata: Mapping[str, str] | None
    flags: MessageFlags


class _Invite(msgspec.Struct):
    code: str


def _invite_code(data: bytes) -> str:
    invite: _Invite = decoder(_Invite).decode(data)
    return invite.code


class Lobbies(ResourceGroup):
    async def add_member(
        self, lobby_id: int, user_id: int, /, **options: Unpack[LobbyMemberAdd]
    ) -> LobbyMember:
        """Add the user to the lobby, or update the member already in it."""
        return await self._call(
            LobbyMember,
            "PUT",
            "/lobbies/{lobby_id}/members/{user_id}",
            json=wire(options, LobbyMemberAdd),
            lobby_id=lobby_id,
            user_id=user_id,
        )

    async def bulk_update_members(
        self, lobby_id: int, /, members: Sequence[LobbyMemberUpdate]
    ) -> tuple[LobbyMember, ...]:
        """Add, update or, with `remove_member`, remove up to 25 members at once."""
        return await self._call(
            each(LobbyMember),
            "POST",
            "/lobbies/{lobby_id}/members/bulk",
            json=members,
            lobby_id=lobby_id,
        )

    async def create(self, **options: Unpack[LobbyCreate]) -> Lobby:
        """Create a lobby, which Social SDK clients cannot join or leave themselves."""
        return await self._call(
            Lobby, "POST", "/lobbies", json=wire(options, LobbyCreate)
        )

    async def create_invite_for_user(self, lobby_id: int, user_id: int, /) -> str:
        """A one-hour, single-use invite code to the lobby's linked channel."""
        return await self._call(
            _invite_code,
            "POST",
            "/lobbies/{lobby_id}/members/{user_id}/invites",
            lobby_id=lobby_id,
            user_id=user_id,
        )

    async def delete(self, lobby_id: int, /) -> None:
        await self._http.send("DELETE", "/lobbies/{lobby_id}", lobby_id=lobby_id)

    async def edit(self, lobby_id: int, /, **options: Unpack[LobbyCreate]) -> Lobby:
        """Change the lobby; `members` replaces every member."""
        return await self._call(
            Lobby,
            "PATCH",
            "/lobbies/{lobby_id}",
            json=wire(options, LobbyCreate),
            lobby_id=lobby_id,
        )

    async def fetch(self, lobby_id: int, /) -> Lobby:
        return await self._call(Lobby, "GET", "/lobbies/{lobby_id}", lobby_id=lobby_id)

    async def remove_member(self, lobby_id: int, user_id: int, /) -> None:
        await self._http.send(
            "DELETE",
            "/lobbies/{lobby_id}/members/{user_id}",
            lobby_id=lobby_id,
            user_id=user_id,
        )

    async def update_message_moderation_metadata(
        self, lobby_id: int, message_id: int, /, metadata: Mapping[str, str]
    ) -> None:
        """Tell the game clients what to do with a lobby message; at most 5 keys."""
        await self._http.send(
            "PUT",
            "/lobbies/{lobby_id}/messages/{message_id}/moderation-metadata",
            json=metadata,
            lobby_id=lobby_id,
            message_id=message_id,
        )


class BearerLobbies(ResourceGroup):
    async def create_invite_for_self(self, lobby_id: int, /) -> str:
        """A one-hour, single-use invite code to the lobby's linked channel."""
        return await self._call(
            _invite_code,
            "POST",
            "/lobbies/{lobby_id}/members/@me/invites",
            lobby_id=lobby_id,
        )

    async def create_or_join(self, **options: Unpack[LobbyCreateOrJoin]) -> Lobby:
        """Join the lobby that has `secret`, or create it."""
        return await self._call(
            Lobby,
            "PUT",
            "/lobbies",
            json=wire(options, LobbyCreateOrJoin),
        )

    async def leave(self, lobby_id: int, /) -> None:
        await self._http.send(
            "DELETE", "/lobbies/{lobby_id}/members/@me", lobby_id=lobby_id
        )

    async def link_channel(self, lobby_id: int, /, channel_id: int) -> Lobby:
        return await self._call(
            Lobby,
            "PATCH",
            "/lobbies/{lobby_id}/channel-linking",
            json={"channel_id": channel_id},
            lobby_id=lobby_id,
        )

    async def list_messages(
        self, lobby_id: int, /, **options: Unpack[LobbyListMessages]
    ) -> tuple[LobbyMessage, ...]:
        """The latest messages, 50 unless `limit` says up to 200."""
        return await self._call(
            each(LobbyMessage),
            "GET",
            "/lobbies/{lobby_id}/messages",
            query=wire(options, LobbyListMessages),
            lobby_id=lobby_id,
        )

    async def send_message(
        self, lobby_id: int, /, content: str, **options: Unpack[LobbySendMessage]
    ) -> LobbyMessage:
        return await self._call(
            LobbyMessage,
            "POST",
            "/lobbies/{lobby_id}/messages",
            json={"content": content} | wire(options, LobbySendMessage),
            lobby_id=lobby_id,
        )

    async def unlink_channel(self, lobby_id: int, /) -> Lobby:
        return await self._call(
            Lobby,
            "PATCH",
            "/lobbies/{lobby_id}/channel-linking",
            json={},
            lobby_id=lobby_id,
        )
