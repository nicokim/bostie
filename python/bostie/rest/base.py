from bostie.cache import Cache
from bostie.http import HTTPClient
from bostie.models import AllowedMentions


class Group:
    """Base of the resource groups on `bot.rest`."""

    def __init__(
        self, http: HTTPClient, allowed_mentions: AllowedMentions, cache: Cache
    ) -> None:
        self._http = http
        self._allowed_mentions = allowed_mentions
        self._cache = cache
