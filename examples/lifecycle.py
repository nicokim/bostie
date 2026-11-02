"""Open a database before connecting, run background tasks, and close it at the end."""

import datetime as dt
import logging
import sqlite3

import bostie
from bostie.events import Starting, Stopped, TaskError

log = logging.getLogger("lifecycle-example")


class StatsBot(bostie.Bot):
    database: sqlite3.Connection


bot = StatsBot()


@bot.listen
async def open_database(event: Starting) -> None:
    bot.database = sqlite3.connect("stats.db")
    bot.database.execute("CREATE TABLE IF NOT EXISTS guilds (at TEXT, count INTEGER)")


@bot.listen
async def close_database(event: Stopped) -> None:
    bot.database.close()


@bot.every(seconds=600)
async def count_guilds() -> None:
    now = dt.datetime.now(dt.UTC).isoformat()
    with bot.database:
        bot.database.execute(
            "INSERT INTO guilds VALUES (?, ?)", (now, len(bot.cache.guilds()))
        )


@bot.every(time=[dt.time(9), dt.time(21)])
async def report() -> None:
    row = bot.database.execute("SELECT max(count) FROM guilds").fetchone()
    log.info("the bot has been in at most %s guilds", row[0])


@bot.listen
async def task_failed(failure: TaskError) -> None:
    log.error("%s failed", failure.task, exc_info=failure.error)


if __name__ == "__main__":
    bot.run()
