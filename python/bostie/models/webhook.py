from bostie.enums import WebhookType
from bostie.models.base import Model, Snowflake
from bostie.models.user import User


class WebhookSourceGuild(Model, Snowflake, kw_only=True):
    """The guild of the announcement channel a webhook follows."""

    id: int
    name: str
    icon: str | None


class WebhookSourceChannel(Model, Snowflake, kw_only=True):
    """The announcement channel a webhook follows."""

    id: int
    name: str


class Webhook(Model, Snowflake, kw_only=True):
    """A webhook; `token` and `url` only come with incoming webhooks."""

    _HIDDEN = frozenset({"url"})

    id: int
    type: WebhookType
    guild_id: int | None = None
    channel_id: int | None
    user: User | None = None
    name: str | None
    avatar: str | None
    token: str | None = None
    application_id: int | None
    source_guild: WebhookSourceGuild | None = None
    source_channel: WebhookSourceChannel | None = None
    url: str | None = None
