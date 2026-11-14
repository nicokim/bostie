import asyncio

import bostie
from bostie import Context

bot = bostie.Bot()


@bot.command
async def countdown(ctx: Context, seconds: int = 3) -> None:
    """Count down in one message."""
    await ctx.respond(f"{seconds}…")
    for left in range(seconds - 1, 0, -1):
        await asyncio.sleep(1)
        await ctx.edit(content=f"{left}…")
    await asyncio.sleep(1)
    await ctx.edit(content="Go!")
    note = await ctx.followup("Only you see this one.", ephemeral=True)
    await asyncio.sleep(5)
    await ctx.delete(note.id)
