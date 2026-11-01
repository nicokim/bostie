from collections.abc import AsyncIterator
from functools import partial
from typing import TypedDict, Unpack

import msgspec

from bostie.models import Message, User, decode
from bostie.models.base import decoder
from bostie.rest.base import Group, paginate, wire


class PollListAnswerVoters(TypedDict, total=False):
    after: int
    limit: int


class _Voters(msgspec.Struct):
    users: tuple[msgspec.Raw, ...]


class Polls(Group):
    async def end(self, channel_id: int, message_id: int, /) -> Message:
        message = await self._http._decoded(
            partial(decode, Message),
            "POST",
            "/channels/{channel_id}/polls/{message_id}/expire",
            channel_id=channel_id,
            message_id=message_id,
        )
        return self._in_guild(message)

    def list_answer_voters(
        self,
        channel_id: int,
        message_id: int,
        answer_id: int,
        /,
        **options: Unpack[PollListAnswerVoters],
    ) -> AsyncIterator[User]:
        pages = paginate(
            self._http,
            "/channels/{channel_id}/polls/{message_id}/answers/{answer_id}",
            wire(options, PollListAnswerVoters),
            decode=_voters,
            items=tuple,
            cursor=lambda user: user.id,
            page_size=100,
            forward=True,
            channel_id=channel_id,
            message_id=message_id,
            answer_id=answer_id,
        )
        return (user async for page in pages for user in page)


def _voters(data: bytes) -> tuple[User, ...]:
    voters: _Voters = decoder(_Voters).decode(data)
    return tuple(decode(User, bytes(user)) for user in voters.users)
