from collections.abc import AsyncIterator
from typing import TypedDict, Unpack

from bostie.models import Message, User
from bostie.rest.base import ResourceGroup, each, wire


class PollListAnswerVoters(TypedDict, total=False):
    after: int
    limit: int


class Polls(ResourceGroup):
    async def end(self, channel_id: int, message_id: int, /) -> Message:
        message = await self._call(
            Message,
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
        pages = self._paginate(
            "/channels/{channel_id}/polls/{message_id}/answers/{answer_id}",
            wire(options, PollListAnswerVoters),
            decodes=each(User, "users"),
            items=tuple,
            cursor=lambda user: user.id,
            page_size=100,
            forward=True,
            channel_id=channel_id,
            message_id=message_id,
            answer_id=answer_id,
        )
        return (user async for page in pages for user in page)
