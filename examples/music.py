"""A music queue per guild: play direct media URLs, skip, loop and leave."""

from typing import Literal

import bostie
from bostie import Context, voice

bot = bostie.Bot(voice=voice.Defaults(timeout=15.0))
queues: dict[int, voice.Queue] = {}


async def queue_for(ctx: Context) -> voice.Queue:
    vc = ctx.voice.current or await ctx.voice.connect()
    queue = queues.get(vc.guild_id)
    if queue is None or queue.vc is not vc:
        queue = queues[vc.guild_id] = voice.Queue(vc, crossfade=2.0)
    return queue


@bot.command
async def play(ctx: Context, url: str) -> None:
    """Queue a direct media URL, such as an MP3 or an HLS playlist."""
    queue = await queue_for(ctx)
    queue.add(voice.Url(url))
    await ctx.respond(f"Queued; {len(queue.upcoming)} up next.")


@bot.command
async def skip(ctx: Context) -> None:
    """Skip the current track."""
    if (queue := queues.get(ctx.guild_id or 0)) is not None:
        queue.skip()
    await ctx.respond("Skipped.")


@bot.command
async def loop(ctx: Context, mode: Literal["off", "track", "queue"]) -> None:
    """Repeat the current track or the whole queue."""
    if (queue := queues.get(ctx.guild_id or 0)) is not None:
        queue.loop = None if mode == "off" else mode
    await ctx.respond(f"Loop: {mode}.")


@bot.command
async def leave(ctx: Context) -> None:
    """Stop playing and leave the voice channel."""
    if (vc := ctx.voice.current) is not None:
        queues.pop(vc.guild_id, None)
        await vc.disconnect()
    await ctx.respond("Bye.")


if __name__ == "__main__":
    bot.run()
