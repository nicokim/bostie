"""A module of commands that bot.py includes, and reloads without a restart."""

from bostie import Context, Router

router = Router()


@router.command
async def hello(ctx: Context) -> None:
    """Say hello."""
    await ctx.respond(f"Hello, {ctx.user.display_name}!")
