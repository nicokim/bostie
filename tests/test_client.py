import asyncio

import pytest
from fakediscord import FakeDiscord, payloads, run

from bostie import RESTClient, StateError, SyncWebhookClient, WebhookClient


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


def test_a_webhook_client_posts_from_its_url_alone() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        url = "https://discord.com/api/webhooks/1/secret-token"
        sent = payloads.fixture("rest/message") | {"webhook_id": "1"}
        async with WebhookClient(url, api_url=fake.api_url) as webhook:
            fake.fail("POST", "/webhooks/1/secret-token", 200, sent)
            message = await webhook.execute("deploy done")
            fake.fail("POST", "/webhooks/1/secret-token/slack", 200, "ok")
            await webhook.execute_slack({"text": "deploy done"}, wait=True)
            with pytest.raises(StateError, match="has no token"):
                await webhook.http.request("GET", "/users/@me")
        execute, slack = fake.requests
        assert message.id == int(sent["id"])
        assert (execute.path, execute.query) == (
            "/webhooks/1/secret-token",
            {"wait": "true"},
        )
        assert (slack.path, slack.query, slack.json) == (
            "/webhooks/1/secret-token/slack",
            {"wait": "true"},
            {"text": "deploy done"},
        )
        assert "authorization" not in execute.headers | slack.headers
        with pytest.raises(ValueError, match="is not a webhook URL"):
            WebhookClient("https://discord.com/channels/1/2")

    run(scenario)


def test_a_sync_webhook_client_runs_its_own_event_loop() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        url = "https://discord.com/api/webhooks/1/secret-token"
        sent = payloads.fixture("rest/message") | {"webhook_id": "1"}
        webhook = SyncWebhookClient(url, api_url=fake.api_url)
        fake.fail("POST", "/webhooks/1/secret-token", 200, sent)
        message = await asyncio.to_thread(webhook.execute, "deploy done")
        assert message.id == int(sent["id"])
        assert fake.requests[-1].json["content"] == "deploy done"

    run(scenario)
