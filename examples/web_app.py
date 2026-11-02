"""Run the bot inside an aiohttp web app, whose route posts through the bot's REST."""

import asyncio
from collections.abc import AsyncIterator

from aiohttp import web

import bostie
from bostie import StateError

bot = bostie.Bot()


async def run_bot(app: web.Application) -> AsyncIterator[None]:
    started = asyncio.create_task(bot.start())
    try:
        await bot.wait_until_ready()
    except StateError:
        await started
        raise
    yield
    await bot.close()
    await started


async def announce(request: web.Request) -> web.Response:
    channel_id = int(request.match_info["channel_id"])
    text = (await request.json())["text"]
    message = await bot.rest.messages.create(channel_id, text)
    return web.json_response({"id": str(message.id)})


app = web.Application()
app.cleanup_ctx.append(run_bot)
app.router.add_post("/channels/{channel_id}/announce", announce)


if __name__ == "__main__":
    web.run_app(app)
