from collections.abc import Iterable, Mapping
from typing import Literal
from urllib.parse import quote, urlencode

from bostie.enums import ApplicationIntegrationType, Permissions

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"

type QueryValue = str | int | bool | None


def authorize_url(
    client_id: int,
    *,
    scopes: Iterable[str],
    redirect_uri: str | None = None,
    state: str | None = None,
    prompt: Literal["consent", "none"] | None = None,
    integration_type: ApplicationIntegrationType | None = None,
    code_challenge: str | None = None,
    response_type: Literal["code", "token"] = "code",
) -> str:
    """The URL that asks a user to grant `scopes` and redirects back with a code.

    `code_challenge` is the S256 PKCE challenge of the verifier the code exchange
    sends; `response_type="token"` redirects with a token instead of a code.
    """
    return _url(
        {
            "response_type": response_type,
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
    scopes: Iterable[str] | None = None,
    integration_type: ApplicationIntegrationType | None = None,
    guild_id: int | None = None,
    disable_guild_select: bool | None = None,
) -> str:
    """The URL that installs the app; what it leaves out the Developer Portal sets."""
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


def _url(query: Mapping[str, QueryValue]) -> str:
    pairs = {name: _text(value) for name, value in query.items() if value is not None}
    return f"{AUTHORIZE_URL}?{urlencode(pairs, quote_via=quote)}"


def _text(value: str | int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if isinstance(value, str) else str(int(value))
