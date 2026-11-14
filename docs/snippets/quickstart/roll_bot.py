import secrets

import bostie
from bostie import Context, Range

bot = bostie.Bot()


@bot.command
async def roll(ctx: Context, sides: Range[int, 2, 100] = 6) -> None:
    """Roll a die."""
    rolled = secrets.randbelow(sides) + 1
    await ctx.respond(f"You rolled {rolled} on a d{sides}.")


bot.run()
