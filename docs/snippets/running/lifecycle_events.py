import logging

import bostie
from bostie.events import Started, Starting, Stopped, Stopping

log = logging.getLogger("my-bot")
bot = bostie.Bot()


@bot.listen
async def starting(event: Starting) -> None:
    application = await bot.rest.application.fetch()
    log.info("starting %s", application.name)


@bot.listen
async def started(event: Started) -> None:
    log.info("ready in %d guilds, latency %s s", len(bot.cache.guilds()), bot.latency)


@bot.listen
async def stopping(event: Stopping) -> None:
    log.info("saying goodbye while the gateway is still open")


@bot.listen
async def stopped(event: Stopped) -> None:
    log.info("the gateway and REST are closed")


bot.run()
