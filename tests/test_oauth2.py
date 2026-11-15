import pytest

from bostie import (
    ApplicationIntegrationType,
    BearerClient,
    OAuth2Scope,
    Permissions,
    oauth2,
)
from bostie.testing import FakeDiscord, run

TOKEN = {
    "access_token": "user-token",
    "token_type": "Bearer",
    "expires_in": 604800,
    "refresh_token": "refresh",
    "scope": "identify role_connections.write",
}


@pytest.mark.parametrize(
    ("url", "query"),
    [
        (
            oauth2.authorize_url(
                1,
                scopes=[OAuth2Scope.IDENTIFY, "guilds.join"],
                redirect_uri="https://example.com/back",
                state="s",
                prompt="consent",
                integration_type=ApplicationIntegrationType.USER_INSTALL,
                code_challenge="abc",
            ),
            (
                "response_type=code&client_id=1&scope=identify%20guilds.join"
                "&redirect_uri=https%3A%2F%2Fexample.com%2Fback&state=s&prompt=consent"
                "&integration_type=1&code_challenge=abc&code_challenge_method=S256"
            ),
        ),
        (
            oauth2.invite_url(
                1,
                permissions=Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES,
                scopes=[OAuth2Scope.BOT],
                guild_id=5,
                disable_guild_select=True,
            ),
            (
                "client_id=1&scope=bot&permissions=3072&guild_id=5"
                "&disable_guild_select=true"
            ),
        ),
        (oauth2.invite_url(1), "client_id=1"),
    ],
)
def test_authorization_urls_carry_only_the_parts_given(url: str, query: str) -> None:
    assert url == f"https://discord.com/oauth2/authorize?{query}"


def test_a_code_becomes_a_token_whose_bearer_sets_a_role_connection() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with BearerClient(api_url=fake.api_url) as anonymous:
            fake.fail("POST", "/oauth2/token", 200, TOKEN)
            token = await anonymous.rest.oauth2.exchange_code(
                client_id=1,
                client_secret="secret",
                code="code",
                redirect_uri="https://example.com/back",
            )
        assert "user-token" not in repr(token)
        async with BearerClient(token.access_token, api_url=fake.api_url) as user:
            connection = {"platform_name": "Chess", "metadata": {"rating": "1800"}}
            fake.fail(
                "PUT", "/users/@me/applications/1/role-connection", 200, connection
            )
            updated = await user.rest.users.update_role_connection(
                1, platform_name="Chess", metadata={"rating": "1800"}
            )
        exchanged, set_connection = fake.requests[-2:]
        assert exchanged.json == {
            "client_id": "1",
            "client_secret": "secret",
            "grant_type": "authorization_code",
            "code": "code",
            "redirect_uri": "https://example.com/back",
        }
        assert "authorization" not in exchanged.headers
        assert set_connection.json == connection
        assert updated.metadata == {"rating": "1800"}

    run(scenario)
