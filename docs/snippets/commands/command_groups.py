import bostie
from bostie import Context, Permissions, ResolvedMember, Role

bot = bostie.Bot()

roles = bot.group("roles", "Manage roles", default_permissions=Permissions.MANAGE_ROLES)
colors = roles.group("color", "Color roles")


@roles.command
async def give(ctx: Context, member: ResolvedMember, role: Role) -> None:
    """Give a member a role."""
    await ctx.respond(f"/{ctx.command_name}: {member.mention} gets {role.mention}.")


@colors.command(name="set")
async def set_color(ctx: Context, member: ResolvedMember, hex_code: str) -> None:
    """Give a member a color role."""
    await ctx.respond(f"/{ctx.command_name}: {member.mention} is now {hex_code}.")
