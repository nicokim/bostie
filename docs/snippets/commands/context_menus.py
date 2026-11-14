import bostie
from bostie import Context, Message, ResolvedMember, User, WebhookMessage, fmt

bot = bostie.Bot()


@bot.user_command("Account age")
async def account_age(ctx: Context, user: User) -> None:
    created = fmt.timestamp(bostie.snowflake_time(user.id), "R")
    await ctx.respond(f"{user.mention} joined Discord {created}.", ephemeral=True)


@bot.user_command("Joined server")
async def joined(ctx: Context, member: ResolvedMember) -> None:
    if member.joined_at is None:
        await ctx.respond(f"Discord sent no join date for {member.mention}.")
        return
    await ctx.respond(f"{member.mention} joined {fmt.timestamp(member.joined_at)}.")


@bot.message_command("Word count")
async def word_count(ctx: Context, message: Message | WebhookMessage) -> None:
    await ctx.respond(f"{len(message.content.split())} words.", ephemeral=True)
