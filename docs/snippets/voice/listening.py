import logging

import bostie
from bostie import Context, voice

log = logging.getLogger("my-bot")
bot = bostie.Bot()


@bot.command
async def meter(ctx: Context, seconds: int = 10) -> None:
    """Log how loud each speaker is for a few seconds."""
    vc = ctx.voice.current or await ctx.voice.connect()
    await ctx.respond("Listening.")
    loudest: dict[int, float] = {}
    async with vc.receive(voice.PCM(rate=16_000, channels=1)) as frames:
        async for frame in frames:
            if frame.user_id is not None and frame.level is not None:
                level = max(loudest.get(frame.user_id, frame.level), frame.level)
                loudest[frame.user_id] = level
            if frame.offset >= seconds * 48_000:
                break
    for user_id, level in loudest.items():
        log.info("user %d peaked at %.1f dBFS", user_id, level)


@bot.command
async def transcribe(ctx: Context) -> None:
    """Collect what each person says, one utterance at a time."""
    vc = ctx.voice.current or await ctx.voice.connect()
    await ctx.respond("Transcribing until the bot leaves.")
    rx = vc.receive(voice.PCM(rate=16_000, channels=1), markers=True)
    async for said in voice.utterances(rx, min_silence=0.6, max_length=20.0):
        log.info(
            "user %d spoke %.1f s (%d bytes of 16 kHz mono)",
            said.user_id,
            said.duration,
            len(said.data),
        )
