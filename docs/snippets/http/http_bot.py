import logging

import bostie
from bostie import Context
from bostie.events import ApplicationAuthorized, EntitlementCreate

log = logging.getLogger("my-bot")
bot = bostie.Bot()
app = bostie.ASGIApp(bot)


@bot.command
async def ping(ctx: Context) -> None:
    """Answer over HTTP."""
    await ctx.respond("pong")


@bot.listen
async def installed(event: ApplicationAuthorized) -> None:
    where = "a guild" if event.guild is not None else "a user account"
    log.info("%s added the app to %s", event.user.username, where)


@bot.listen
async def purchased(event: EntitlementCreate) -> None:
    log.info("new entitlement %d", event.entitlement.id)
