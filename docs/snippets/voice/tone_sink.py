import math
import struct

import bostie
from bostie import Context, voice

bot = bostie.Bot()
RATE = 24_000


def tone(frequency: float, seconds: float) -> bytes:
    count = int(RATE * seconds)
    samples = (
        int(8000 * math.sin(2 * math.pi * frequency * index / RATE))
        for index in range(count)
    )
    return struct.pack(f"<{count}h", *samples)


@bot.command
async def beep(ctx: Context) -> None:
    """Play three generated beeps."""
    vc = ctx.voice.current or await ctx.voice.connect()
    await ctx.respond("Beeping.")
    async with vc.sink(voice.PCM(rate=RATE, channels=1), volume=0.5) as sink:
        for frequency in (440.0, 554.4, 659.3):
            await sink.write(tone(frequency, 0.3))
            await sink.write(bytes(RATE // 10 * 2))
        done = sink.mark()
        await sink.write(tone(880.0, 1.0))
        if await done:
            await ctx.followup("The three beeps played; one long one to go.")
    await ctx.followup(f"Done: {sink.stats().underruns} underruns.")


@bot.command
async def opus(ctx: Context) -> None:
    """Encode in Python and send the packets as they are."""
    vc = ctx.voice.current or await ctx.voice.connect()
    encoder = voice.OpusEncoder(voice.PCM(rate=RATE, channels=1))
    await ctx.respond("Sending.")
    async with vc.sink(voice.Opus()) as sink:
        for packet in [*encoder.encode(tone(330.0, 2.0)), *encoder.flush()]:
            await sink.write(packet)
