import bostie
from bostie import Context, Range

bot = bostie.Bot()


@bot.command
async def remind(ctx: Context, minutes: Range[int, 1, 1440], text: str) -> None:
    """Set a reminder.

    Args:
        minutes: How long to wait, up to a day.
        text: What to remind you of.
    """
    await ctx.respond(f"In {minutes} minutes: {text}", ephemeral=True)
