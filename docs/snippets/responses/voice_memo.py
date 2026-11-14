import bostie
from bostie import Context, voice

bot = bostie.Bot()


@bot.command
async def memo(ctx: Context) -> None:
    """Send today's memo as a voice message."""
    await ctx.respond(files=[await voice.message("memo.ogg")])
