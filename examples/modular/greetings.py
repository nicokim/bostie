"""A module of commands that bot.py includes, and reloads without a restart.

Its router keeps them to servers and answers so that only the caller sees it.
"""

from bostie import Context, InteractionContextType, Router

router = Router(contexts=[InteractionContextType.GUILD], ephemeral=True)


@router.command
async def hello(ctx: Context) -> None:
    """Say hello."""
    await ctx.respond(f"Hello, {ctx.user.display_name}!")
