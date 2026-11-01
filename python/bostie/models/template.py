from datetime import datetime
from typing import Any

from bostie.models.base import Model
from bostie.models.user import User


class GuildTemplate(Model, kw_only=True):
    """A snapshot of a guild that new guilds can be created from."""

    code: str
    name: str
    description: str | None
    usage_count: int
    creator_id: int
    creator: User
    created_at: datetime
    updated_at: datetime
    source_guild_id: int
    serialized_source_guild: dict[str, Any]
    is_dirty: bool | None
