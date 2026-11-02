"""A bot split into modules: greetings.py is included by name and reloads on /reload."""

import bostie
from bostie import Context, Permissions

bot = bostie.Bot()
bot.include("greetings")


@bot.command(default_permissions=Permissions.ADMINISTRATOR, ephemeral=True)
async def reload(ctx: Context) -> None:
    """Reload the greetings module from disk."""
    bot.reload("greetings")
    await bot.sync_commands()
    await ctx.respond("Reloaded.")


if __name__ == "__main__":
    bot.run()
