import asyncio

import bostie
from bostie import Context

bot = bostie.Bot()


def weekly_summary() -> str:
    return "12 new members and 340 messages this week."


@bot.command
async def report(ctx: Context) -> None:
    """Write this week's report."""
    await ctx.respond("Writing the report.")
    async with ctx.typing():
        summary = await asyncio.to_thread(weekly_summary)
    await ctx.followup(summary)
