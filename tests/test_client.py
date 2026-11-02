import pytest
from fakediscord import FakeDiscord, run

from bostie import RESTClient, StateError


def test_a_rest_client_reaches_the_api_without_a_gateway() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        client = RESTClient(fake.token, api_url=fake.api_url)
        with pytest.raises(StateError, match="works inside 'async with'"):
            _ = client.rest
        async with client:
            me = await client.rest.users.fetch_current()
        assert me.id == int(fake.bot_user["id"])
        assert fake.requests[-1].headers["authorization"] == f"Bot {fake.token}"
        assert fake.connections == []
        with pytest.raises(StateError):
            _ = client.http

    run(scenario)
