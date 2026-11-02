"""Serve slash commands over HTTP: run `uvicorn http_interactions:app` in this folder.

Set the Interactions Endpoint URL in the Developer Portal to where the app listens.
"""

import bostie
from bostie import Context

bot = bostie.Bot()
app = bostie.ASGIApp(bot)


@bot.command
async def ping(ctx: Context) -> None:
    """Answer pong over HTTP."""
    await ctx.respond("pong")
