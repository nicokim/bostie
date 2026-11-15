import bostie
from bostie import Context, Intents, PartialChannel, Permissions, fmt

bot = bostie.Bot(
    intents=Intents.DEFAULT | Intents.GUILD_MEMBERS,
    members=True,
    max_messages=5000,
)


@bot.command
async def server(ctx: Context) -> None:
    """Describe this server from the cache."""
    guild = bot.cache.guild(ctx.guild_id or 0)
    if guild is None:
        await ctx.respond("I only know servers I am in.", ephemeral=True)
        return
    channels = bot.cache.guild_channels(guild.id)
    in_voice = bot.cache.guild_voice_states(guild.id)
    members = bot.cache.guild_members(guild.id)
    created = fmt.timestamp(guild.created_at, "R")
    await ctx.respond(
        f"{guild.name}: {len(channels)} channels, {len(members)} members, "
        f"{len(in_voice)} in voice, created {created}"
    )


@bot.command
async def can_post(ctx: Context, channel: PartialChannel) -> None:
    """Check whether the bot can post in a channel."""
    allowed = bot.cache.permissions(channel.id)
    if allowed is None:
        await ctx.respond("I do not know that channel.", ephemeral=True)
    elif Permissions.SEND_MESSAGES in allowed:
        await ctx.respond(f"I can post in {channel.mention}.", ephemeral=True)
    else:
        await ctx.respond(f"I cannot post in {channel.mention}.", ephemeral=True)
