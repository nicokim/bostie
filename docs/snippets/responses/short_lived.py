import bostie
from bostie import Context

bot = bostie.Bot()


@bot.command
async def nudge(ctx: Context) -> None:
    """Remind the channel to stay on topic."""
    await ctx.respond("Please keep this channel on topic.", delete_after=30)
