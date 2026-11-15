import bostie
from bostie import Context

bot = bostie.Bot()
EVENTS_GUILD = 1335172792320131073


@bot.command(name="rules")
async def rules(ctx: Context) -> None:
    """Show the rules of the bot."""
    await ctx.respond("Be kind and stay on topic.")


@bot.command(name="rules", guilds=[EVENTS_GUILD])
async def event_rules(ctx: Context) -> None:
    """Show the rules of this server's events."""
    await ctx.respond("Sign up before the event starts.")
