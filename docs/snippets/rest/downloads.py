import os

import bostie
from bostie import Context, Message, WebhookMessage, cdn

ARCHIVE_CHANNEL_ID = int(os.environ.get("ARCHIVE_CHANNEL_ID", "0"))

bot = bostie.Bot()


@bot.message_command("Archive files")
async def archive(ctx: Context, message: Message | WebhookMessage) -> None:
    await ctx.defer(ephemeral=True)
    files = [await bot.http.to_file(attachment) for attachment in message.attachments]
    if files:
        await bot.rest.messages.create(
            ARCHIVE_CHANNEL_ID, f"From {message.jump_url}", files=files
        )
    await ctx.respond(f"Archived {len(files)} files.")


@bot.command
async def avatar(ctx: Context) -> None:
    """Send your avatar as a file."""
    url = cdn.display_avatar(ctx.user, size=1024, format="png")
    await ctx.respond(files=[await bot.http.to_file(url)])
