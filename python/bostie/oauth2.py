from collections.abc import Iterable, Mapping
from typing import Literal
from urllib.parse import quote, urlencode

from bostie.enums import ApplicationIntegrationType, OAuth2Scope, Permissions

_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"

type _QueryValue = str | int | bool | None


def authorize_url(
    client_id: int,
    *,
    scopes: Iterable[OAuth2Scope | str],
    redirect_uri: str | None = None,
    state: str | None = None,
    prompt: Literal["consent", "none"] | None = None,
    integration_type: ApplicationIntegrationType | None = None,
    code_challenge: str | None = None,
) -> str:
    """The code grant URL for `scopes`; `code_challenge` is an S256 PKCE challenge."""
    return _url(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": " ".join(scopes),
            "redirect_uri": redirect_uri,
            "state": state,
            "prompt": prompt,
            "integration_type": integration_type,
            "code_challenge": code_challenge,
            "code_challenge_method": None if code_challenge is None else "S256",
        }
    )


def invite_url(
    client_id: int,
    *,
    permissions: Permissions | None = None,
    scopes: Iterable[OAuth2Scope | str] | None = None,
    integration_type: ApplicationIntegrationType | None = None,
    guild_id: int | None = None,
    disable_guild_select: bool | None = None,
) -> str:
    """The URL that installs the app; its Developer Portal settings fill in the rest."""
    return _url(
        {
            "client_id": client_id,
            "scope": None if scopes is None else " ".join(scopes),
            "permissions": None if permissions is None else int(permissions),
            "integration_type": integration_type,
            "guild_id": guild_id,
            "disable_guild_select": disable_guild_select,
        }
    )


def _url(query: Mapping[str, _QueryValue]) -> str:
    pairs = {name: _text(value) for name, value in query.items() if value is not None}
    return f"{_AUTHORIZE_URL}?{urlencode(pairs, quote_via=quote)}"


def _text(value: str | int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if isinstance(value, str) else str(int(value))
