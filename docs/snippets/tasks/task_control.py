import logging

import bostie
from bostie import Context
from bostie.checks import is_owner

log = logging.getLogger("my-bot")
bot = bostie.Bot()


@bot.every(seconds=60)
async def check_feeds() -> None:
    log.info("checking the feeds, run %d", check_feeds.count)


@check_feeds.before
async def open_feeds() -> None:
    log.info("feed checks started")


@check_feeds.after
async def close_feeds() -> None:
    log.info("feed checks stopped")


@bot.command(checks=[is_owner])
async def feeds(ctx: Context, minutes: int | None = None) -> None:
    """Pause feed checks, or check every so many minutes."""
    if minutes is None:
        check_feeds.stop()
        await ctx.respond("Feed checks paused.", ephemeral=True)
        return
    check_feeds.change_interval(seconds=minutes * 60)
    check_feeds.start()
    await ctx.respond(f"Checking feeds every {minutes} minutes.", ephemeral=True)
