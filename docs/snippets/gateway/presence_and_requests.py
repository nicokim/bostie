import bostie
from bostie import Context, Intents, Status, activity

bot = bostie.Bot(
    intents=Intents.DEFAULT | Intents.GUILD_MEMBERS,
    status=Status.IDLE,
    activities=[activity.custom("Starting up")],
)


@bot.command
async def online(ctx: Context) -> None:
    """Switch to online and show what the bot is doing."""
    await bot.update_presence(
        status=Status.ONLINE, activities=[activity.listening("/help")]
    )
    await ctx.respond("Online.", ephemeral=True)


@bot.command
async def find(ctx: Context, name: str) -> None:
    """Find members whose name starts with the text."""
    if ctx.guild_id is None:
        await ctx.respond("Use this in a server.", ephemeral=True)
        return
    found = await bot.request_members(ctx.guild_id, query=name, limit=10)
    names = ", ".join(member.display_name for member in found.members) or "nobody"
    await ctx.respond(f"Found {names}.", ephemeral=True)


@bot.command
async def sounds(ctx: Context) -> None:
    """List this server's soundboard sounds."""
    if ctx.guild_id is not None:
        found = await bot.request_soundboard_sounds(ctx.guild_id)
        await ctx.respond(", ".join(sound.name for sound in found) or "No sounds.")
