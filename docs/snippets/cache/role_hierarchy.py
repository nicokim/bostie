import bostie
from bostie import Context, Embed, Permissions, ResolvedMember
from bostie.permissions import can_act_on, display_colors, top_role

bot = bostie.Bot()


@bot.command(default_permissions=Permissions.KICK_MEMBERS)
async def kick(ctx: Context, member: ResolvedMember) -> None:
    """Kick a member whom both you and the bot outrank."""
    guild = bot.cache.guild(ctx.guild_id or 0)
    me = bot.cache.me(ctx.guild_id or 0)
    if guild is None or me is None or ctx.member is None:
        await ctx.respond("Use this in a server I am in.", ephemeral=True)
        return
    if not (can_act_on(guild, ctx.member, member) and can_act_on(guild, me, member)):
        await ctx.respond(f"{member.mention} ranks too high.", ephemeral=True)
        return
    await bot.rest.members.remove(
        guild.id, member.id, reason=f"/kick by {ctx.user.username}"
    )
    await ctx.respond(f"Kicked {member.display_name}.", ephemeral=True)


@bot.command
async def rank(ctx: Context, member: ResolvedMember) -> None:
    """Show a member's highest role in its color."""
    guild = bot.cache.guild(ctx.guild_id or 0)
    if guild is None:
        await ctx.respond("Use this in a server I am in.", ephemeral=True)
        return
    role = top_role(guild, member)
    colors = display_colors(guild, member)
    embed = Embed(
        description=f"{member.mention}: {role.mention if role else '@everyone'}",
        color=colors.primary_color if colors else None,
    )
    await ctx.respond(embeds=[embed], ephemeral=True)
