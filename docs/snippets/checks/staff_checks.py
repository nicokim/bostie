import bostie
from bostie import Context, ResolvedMember, Router
from bostie.checks import has_any_role, has_role, is_owner

bot = bostie.Bot()
owner = Router()
owner.check(is_owner)
MODERATOR_ROLE = 1335174302269571073


@owner.command
async def reload(ctx: Context, module: str) -> None:
    """Load a module of the bot again."""
    bot.reload(module)
    await ctx.respond(f"Reloaded {module}.", ephemeral=True)


@bot.command(checks=[has_role(MODERATOR_ROLE)])
async def warn(ctx: Context, member: ResolvedMember) -> None:
    """Warn a member."""
    await ctx.respond(f"{member.mention}, please follow the rules.")


@bot.command(checks=[has_any_role("Admin", "Event Host")])
async def announce(ctx: Context, text: str) -> None:
    """Post an announcement."""
    await ctx.respond(text)


bot.include(owner)
