from datetime import datetime

from bostie.enums import ConnectionService, ConnectionVisibility, OAuth2Scope
from bostie.models.application import PartialApplication
from bostie.models.base import Model
from bostie.models.guild import Guild, PartialIntegration
from bostie.models.user import User
from bostie.models.webhook import Webhook


class AccessToken(Model, kw_only=True):
    """An OAuth2 access token; `scope` lists the granted scopes, space-separated.

    A code grant with the `bot` scope also returns the `guild` the bot joined, and
    one with `webhook.incoming` the `webhook` it created.
    """

    access_token: str
    token_type: str
    expires_in: int
    scope: str
    refresh_token: str | None = None
    id_token: str | None = None
    guild: Guild | None = None
    webhook: Webhook | None = None


class Authorization(Model, kw_only=True):
    """What an access token grants; `user` comes with the `identify` scope."""

    application: PartialApplication
    scopes: tuple[OAuth2Scope, ...]
    expires: datetime
    user: User | None = None


class Connection(Model, kw_only=True):
    """An account on another service that the user linked to Discord."""

    id: str
    name: str
    type: ConnectionService
    revoked: bool = False
    integrations: tuple[PartialIntegration, ...] = ()
    verified: bool
    friend_sync: bool
    show_activity: bool
    two_way_link: bool
    visibility: ConnectionVisibility


class ApplicationRoleConnection(Model, kw_only=True):
    """The user's values for the app's linked role metadata, as strings."""

    platform_name: str | None = None
    platform_username: str | None = None
    metadata: dict[str, str] = {}
