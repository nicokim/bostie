import asyncio

import bostie
from bostie import Context

bot = bostie.Bot(auto_defer=2.0)


@bot.command
async def report(ctx: Context) -> None:
    """Build a slow report."""
    await ctx.defer(ephemeral=True)
    await asyncio.sleep(10)
    await ctx.respond("The report is ready.")


@bot.command(auto_defer=None)
async def instant(ctx: Context) -> None:
    """Never deferred automatically."""
    await ctx.respond("Done.")
