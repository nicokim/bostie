import datetime as dt
import logging

import bostie
from bostie import activity
from bostie.events import TaskError

log = logging.getLogger("my-bot")
bot = bostie.Bot()
TOKYO = dt.timezone(dt.timedelta(hours=9))


@bot.every(seconds=300)
async def show_guild_count() -> None:
    count = len(bot.cache.guilds())
    await bot.update_presence(activities=[activity.watching(f"{count} servers")])


@bot.every(time=[dt.time(9, tzinfo=TOKYO), dt.time(21, tzinfo=TOKYO)])
async def remind() -> None:
    log.info("twice a day, at 9:00 and 21:00 in Tokyo")


@bot.every(time=dt.time(0, 0))
async def midnight_utc() -> None:
    log.info("every day at midnight UTC")


@bot.listen
async def task_failed(failure: TaskError) -> None:
    log.error("%s failed", failure.task, exc_info=failure.error)
