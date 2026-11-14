import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import bostie
from bostie import Context, voice

bot = bostie.Bot()
CHIME = Path("chime.ogg")


@bot.command
async def chime(ctx: Context) -> None:
    """Play a short sound from memory."""
    vc = ctx.voice.current or await ctx.voice.connect()
    data = await asyncio.to_thread(CHIME.read_bytes)
    await vc.play(voice.Bytes(data, hint="ogg"))
    await ctx.respond("Ding.")


@bot.command
async def radio(ctx: Context) -> None:
    """Play a station through ffmpeg."""
    vc = ctx.voice.current or await ctx.voice.connect()
    station = voice.FFmpeg(["-i", "https://example.com/radio.m3u8"])
    await vc.play(station)
    await ctx.respond("On air.")


async def silence(seconds: float) -> AsyncIterator[bytes]:
    frame = bytes(3840)
    for _ in range(int(seconds * 50)):
        yield frame


@bot.command
async def hush(ctx: Context) -> None:
    """Play three seconds of generated silence."""
    vc = ctx.voice.current or await ctx.voice.connect()
    track = await vc.play(voice.Stream(silence(3), voice.PCM()))
    await ctx.respond("Shh.")
    await track.wait()
