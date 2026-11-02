"""A script that uses the REST API without a gateway: list the bot's guilds."""

import asyncio
import logging

import bostie

log = logging.getLogger("rest-example")


async def main() -> None:
    async with bostie.RESTClient() as client:
        me = await client.rest.users.fetch_current()
        async for guild in client.rest.guilds.list():
            log.info("%s is in %s", me.username, guild.name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
