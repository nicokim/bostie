import bostie
from bostie import Context, Message, Permissions, Range, User, WebhookMessage

bot = bostie.Bot()


@bot.command(default_permissions=Permissions.MANAGE_MESSAGES)
async def purge(
    ctx: Context, count: Range[int, 1, 1000], user: User | None = None
) -> None:
    """Delete recent messages here, or only those of one user."""
    if ctx.channel_id is None:
        await ctx.respond("Use this in a channel.", ephemeral=True)
        return

    def wanted(message: Message | WebhookMessage) -> bool:
        return user is None or message.author.id == user.id

    await ctx.defer(ephemeral=True)
    deleted = await bot.rest.messages.purge(
        ctx.channel_id,
        limit=count,
        check=wanted,
        reason=f"/purge by {ctx.user.username}",
    )
    await ctx.respond(f"Deleted {len(deleted)} messages.")
