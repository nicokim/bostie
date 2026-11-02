"""A linked roles verification page: OAuth2 login, then the user's role connection.

Set the Linked Roles Verification URL to http://localhost:8080/login, and the
redirect URL to http://localhost:8080/callback, in the Developer Portal.
"""

import os
import secrets

from aiohttp import web

import bostie
from bostie import oauth2

CLIENT_ID = int(os.environ.get("CLIENT_ID", "0"))
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:8080/callback"
STATES: set[str] = set()


async def login(request: web.Request) -> web.Response:
    state = secrets.token_urlsafe(16)
    STATES.add(state)
    url = oauth2.authorize_url(
        CLIENT_ID,
        scopes=["identify", "role_connections.write"],
        redirect_uri=REDIRECT_URI,
        state=state,
    )
    raise web.HTTPFound(url)


async def callback(request: web.Request) -> web.Response:
    if request.query.get("state") not in STATES:
        raise web.HTTPForbidden
    STATES.discard(request.query["state"])
    async with bostie.BearerClient() as anonymous:
        token = await anonymous.rest.oauth2.exchange_code(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            code=request.query["code"],
            redirect_uri=REDIRECT_URI,
        )
    async with bostie.BearerClient(token.access_token) as user:
        me = await user.rest.users.fetch_current()
        await user.rest.users.update_role_connection(
            CLIENT_ID,
            platform_name="Example",
            platform_username=me.username,
            metadata={"verified": "1"},
        )
    return web.Response(text=f"Verified, {me.username}. You can close this tab.")


app = web.Application()
app.router.add_get("/login", login)
app.router.add_get("/callback", callback)


if __name__ == "__main__":
    web.run_app(app, port=8080)
