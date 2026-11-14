import bostie
from bostie import Context

bot = bostie.Bot()


@bot.command
async def hello(ctx: Context) -> None:
    """Say hello."""
    await ctx.respond(f"Hello, {ctx.user.display_name}!")


bot.run()
