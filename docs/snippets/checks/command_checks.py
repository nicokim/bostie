import bostie
from bostie import CheckFailure, Context, Permissions
from bostie.checks import Cooldown, MaxConcurrency, bot_has_permissions, has_permissions

bot = bostie.Bot()
BANNED_USERS = {80351110224678912}


@bot.check
def not_banned(ctx: Context) -> bool:
    if ctx.user.id in BANNED_USERS:
        raise CheckFailure("You are banned from using this bot.")
    return True


async def in_a_guild(ctx: Context) -> bool:
    return ctx.guild_id is not None


@bot.command(
    checks=[
        in_a_guild,
        has_permissions(Permissions.MANAGE_MESSAGES),
        bot_has_permissions(Permissions.MANAGE_MESSAGES),
        Cooldown(2, 60, per="channel"),
        MaxConcurrency(1, per="guild"),
    ]
)
async def clean(ctx: Context, count: int = 20) -> None:
    """Delete recent messages."""
    await ctx.respond(f"Deleting {count} messages.", ephemeral=True)
