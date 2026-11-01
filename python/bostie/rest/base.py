from bostie.cache import Cache
from bostie.http import HTTPClient
from bostie.models import AllowedMentions
from bostie.models.message import PartialMessage, in_guild


class Group:
    """Base of the resource groups on `bot.rest`."""

    def __init__(
        self, http: HTTPClient, allowed_mentions: AllowedMentions, cache: Cache
    ) -> None:
        self._http = http
        self._allowed_mentions = allowed_mentions
        self._cache = cache

    def _in_guild[M: PartialMessage](self, message: M) -> M:
        channel = self._cache.channel(message.channel_id)
        return in_guild(message, None if channel is None else channel.guild_id)
