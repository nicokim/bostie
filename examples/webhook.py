"""Post through a webhook URL with no bot token, from async code and from plain code."""

import asyncio
import os

import bostie


async def announce(url: str) -> None:
    async with bostie.WebhookClient(url) as webhook:
        message = await webhook.execute("Deploying…")
        await webhook.edit_message(message.id, content="Deployed.")


def announce_from_plain_code(url: str) -> None:
    bostie.SyncWebhookClient(url).execute("Hello from a script without asyncio.")


if __name__ == "__main__":
    url = os.environ["WEBHOOK_URL"]
    asyncio.run(announce(url))
    announce_from_plain_code(url)
