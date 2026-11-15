import logging

import bostie
from bostie import Context, fmt
from bostie.events import ScheduledEventUpdate

log = logging.getLogger("my-bot")
bot = bostie.Bot()


@bot.command
async def planned(ctx: Context) -> None:
    """List this server's scheduled events."""
    lines = [
        f"{event.name} {fmt.timestamp(event.scheduled_start_time, 'R')}"
        for event in bot.cache.guild_scheduled_events(ctx.guild_id or 0)
    ]
    await ctx.respond("\n".join(lines) or "Nothing is planned.", ephemeral=True)


@bot.listen
async def moved(update: ScheduledEventUpdate) -> None:
    old, new = update.old, update.scheduled_event
    if old is not None and old.scheduled_start_time != new.scheduled_start_time:
        log.info("%s moved to %s", new.name, new.scheduled_start_time)
