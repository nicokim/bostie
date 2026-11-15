import bostie
from bostie import Context, Router
from bostie.checks import is_owner

bot = bostie.Bot()
owner = Router()
owner.check(is_owner)


@owner.command
async def reload(ctx: Context, module: str) -> None:
    """Load a module of the bot again."""
    bot.reload(module)
    await ctx.respond(f"Reloaded {module}.", ephemeral=True)


bot.include(owner)
