"""An Activity's entry point command, the button that opens it in the App Launcher.

Turn on Activities for the app in the Developer Portal first: Discord refuses an
entry point command from an app without them.
"""

import bostie
from bostie import Context

bot = bostie.Bot()


@bot.entry_point("Launch")
async def launch(ctx: Context) -> None:
    """Open the game."""
    await ctx.launch_activity()


@bot.command
async def play(ctx: Context) -> None:
    """Open the game from a slash command."""
    await ctx.launch_activity()


if __name__ == "__main__":
    bot.run()
