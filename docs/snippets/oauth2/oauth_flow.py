import base64
import hashlib
import os
import secrets

import bostie
from bostie import (
    AccessToken,
    ApplicationRoleConnectionMetadata,
    ApplicationRoleConnectionMetadataType,
    OAuth2Scope,
    oauth2,
)

CLIENT_ID = int(os.environ.get("CLIENT_ID", "0"))
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
REDIRECT_URI = "https://example.com/callback"


def login_url() -> tuple[str, str, str]:
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    url = oauth2.authorize_url(
        CLIENT_ID,
        scopes=[
            OAuth2Scope.IDENTIFY,
            OAuth2Scope.GUILDS,
            OAuth2Scope.ROLE_CONNECTIONS_WRITE,
        ],
        redirect_uri=REDIRECT_URI,
        state=state,
        code_challenge=challenge,
    )
    return url, state, verifier


async def finish_login(code: str, verifier: str) -> AccessToken:
    async with bostie.BearerClient() as anonymous:
        return await anonymous.rest.oauth2.exchange_code(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            code=code,
            redirect_uri=REDIRECT_URI,
            code_verifier=verifier,
        )


async def describe_user(token: AccessToken) -> str:
    async with bostie.BearerClient(token.access_token) as user:
        me = await user.rest.users.fetch_current()
        guilds = [guild.name async for guild in user.rest.guilds.list()]
    return f"{me.username} is in {len(guilds)} guilds"


async def refresh(token: AccessToken) -> AccessToken:
    if token.refresh_token is None:
        return token
    async with bostie.BearerClient() as anonymous:
        return await anonymous.rest.oauth2.refresh(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            refresh_token=token.refresh_token,
        )


async def register_metadata() -> None:
    async with bostie.RESTClient() as client:
        await client.rest.application.update_role_connection_metadata(
            CLIENT_ID,
            [
                ApplicationRoleConnectionMetadata(
                    type=ApplicationRoleConnectionMetadataType.INTEGER_GREATER_THAN_OR_EQUAL,
                    key="level",
                    name="Level",
                    description="The player's level in the game",
                )
            ],
        )
