import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import bostie
from bostie import ChannelType, Context, File, OverwriteType, Permissions

bot = bostie.Bot()


@bot.command(default_permissions=Permissions.MANAGE_CHANNELS)
async def private_room(ctx: Context, name: str) -> None:
    """Create a channel only you and the bot can see."""
    if ctx.guild_id is None:
        await ctx.respond("Use this in a server.", ephemeral=True)
        return
    me = bot.user.id if bot.user is not None else 0
    channel = await bot.rest.channels.create(
        ctx.guild_id,
        name=name,
        type=ChannelType.GUILD_TEXT,
        permission_overwrites=[
            {
                "id": ctx.guild_id,
                "type": OverwriteType.ROLE,
                "deny": Permissions.VIEW_CHANNEL,
            },
            {
                "id": ctx.user.id,
                "type": OverwriteType.MEMBER,
                "allow": Permissions.VIEW_CHANNEL,
            },
            {"id": me, "type": OverwriteType.MEMBER, "allow": Permissions.VIEW_CHANNEL},
        ],
        reason=f"/private_room by {ctx.user.username}",
    )
    await ctx.respond(f"Created {channel.mention}.", ephemeral=True)


@bot.command
async def today(ctx: Context) -> None:
    """Count the messages sent here in the last 24 hours."""
    if ctx.channel_id is None:
        await ctx.respond("Use this in a channel.", ephemeral=True)
        return
    since = datetime.now(UTC) - timedelta(days=1)
    count = 0
    async for _ in bot.rest.messages.list(ctx.channel_id, after=since, limit=1000):
        count += 1
    await ctx.respond(f"{count} messages in the last 24 hours.")


@bot.command(default_permissions=Permissions.MANAGE_GUILD_EXPRESSIONS)
async def add_logo(ctx: Context) -> None:
    """Upload the logo as an emoji."""
    if ctx.guild_id is None:
        await ctx.respond("Use this in a server.", ephemeral=True)
        return
    image = await asyncio.to_thread(Path("logo.png").read_bytes)
    emoji = await bot.rest.emojis.create(
        ctx.guild_id, name="logo", image=File("logo.png", image)
    )
    await ctx.respond(f"Added <:{emoji.name}:{emoji.id}>.")


@bot.command
async def gateway_url(ctx: Context) -> None:
    """Show what Discord answers on an endpoint, through the raw client."""
    answer = await bot.http.request("GET", "/gateway")
    await ctx.respond(f"The gateway is at {answer['url']}.", ephemeral=True)
