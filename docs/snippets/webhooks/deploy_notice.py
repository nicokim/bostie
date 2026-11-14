import asyncio
import os

import bostie
from bostie import Embed, color, ui


async def announce(url: str, version: str) -> None:
    async with bostie.WebhookClient(url) as webhook:
        message = await webhook.execute(
            embeds=[Embed(title=f"Deploying {version}", color=color.YELLOW)],
            username="Deploy bot",
            avatar_url="https://example.com/deploy.png",
        )
        await webhook.edit_message(
            message.id,
            embeds=[Embed(title=f"Deployed {version}", color=color.GREEN)],
        )
        await webhook.execute(
            components=[ui.text(f"Release notes for {version} are out.")],
            thread_name=f"Release {version}",
            with_components=True,
        )


def announce_from_a_script(url: str) -> None:
    webhook = bostie.SyncWebhookClient(url)
    message = webhook.execute("The nightly backup is running.")
    webhook.edit_message(message.id, content="The nightly backup finished.")


if __name__ == "__main__":
    asyncio.run(announce(os.environ["WEBHOOK_URL"], "2.0"))
