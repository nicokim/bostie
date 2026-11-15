from collections.abc import Iterable
from typing import Literal

from bostie.enums import OAuth2Scope
from bostie.models import AccessToken, Authorization
from bostie.rest._base import ResourceGroup


class OAuth2(ResourceGroup):
    async def exchange_client_credentials(
        self,
        *,
        client_id: int,
        client_secret: str,
        scopes: Iterable[OAuth2Scope | str],
    ) -> AccessToken:
        """A token for the app's owner, or its team, with `scopes`."""
        return await self._call(
            AccessToken,
            "POST",
            "/oauth2/token",
            urlencoded=_client(client_id, client_secret)
            | {"grant_type": "client_credentials", "scope": " ".join(scopes)},
        )

    async def exchange_code(
        self,
        *,
        client_id: int,
        client_secret: str,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> AccessToken:
        """The token for the `code` a user's authorization redirected with."""
        verifier = {} if code_verifier is None else {"code_verifier": code_verifier}
        return await self._call(
            AccessToken,
            "POST",
            "/oauth2/token",
            urlencoded=_client(client_id, client_secret)
            | {"grant_type": "authorization_code", "code": code}
            | {"redirect_uri": redirect_uri}
            | verifier,
        )

    async def fetch_authorization(self) -> Authorization:
        return await self._call(Authorization, "GET", "/oauth2/@me")

    async def refresh(
        self, *, client_id: int, client_secret: str, refresh_token: str
    ) -> AccessToken:
        """A new token for the authorization `refresh_token` belongs to."""
        return await self._call(
            AccessToken,
            "POST",
            "/oauth2/token",
            urlencoded=_client(client_id, client_secret)
            | {"grant_type": "refresh_token", "refresh_token": refresh_token},
        )

    async def revoke(
        self,
        *,
        client_id: int,
        client_secret: str,
        token: str,
        token_type_hint: Literal["access_token", "refresh_token"] | None = None,
    ) -> None:
        """End the authorization of `token`, with every token it issued."""
        hint = {} if token_type_hint is None else {"token_type_hint": token_type_hint}
        await self._http.send(
            "POST",
            "/oauth2/token/revoke",
            urlencoded=_client(client_id, client_secret) | {"token": token} | hint,
        )


def _client(client_id: int, client_secret: str) -> dict[str, str]:
    return {"client_id": str(client_id), "client_secret": client_secret}
