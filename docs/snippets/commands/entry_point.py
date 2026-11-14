import bostie
from bostie import Context

bot = bostie.Bot()


@bot.entry_point("Launch", description="Open the game")
async def launch(ctx: Context) -> None:
    await ctx.launch_activity()
