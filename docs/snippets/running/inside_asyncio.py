import asyncio
import logging

import bostie
from bostie import StateError

log = logging.getLogger("my-bot")
bot = bostie.Bot(shutdown_grace=10.0)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    running = asyncio.create_task(bot.start())
    try:
        await bot.wait_until_ready()
    except StateError:
        await running
        raise
    log.info("%s is in %d guilds", bot.user, len(bot.cache.guilds()))
    try:
        await running
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
