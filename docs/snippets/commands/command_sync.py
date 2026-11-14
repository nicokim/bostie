import os

import bostie
from bostie import Context, Permissions

dev_guild = int(os.environ.get("DEV_GUILD", "0"))
bot = bostie.Bot(dev_guilds=[dev_guild] if dev_guild else [])


@bot.command
async def about(ctx: Context) -> None:
    """Explain how to use the bot."""
    sync = await bot.command_mention("sync")
    await ctx.respond(f"Admins can run {sync} after an update.")


@bot.command(default_permissions=Permissions.ADMINISTRATOR, ephemeral=True)
async def sync(ctx: Context) -> None:
    """Send the commands to Discord again."""
    results = await bot.sync_commands()
    changed = sum(len(r.added) + len(r.removed) + len(r.changed) for r in results)
    await ctx.respond(f"{changed} commands changed.")
