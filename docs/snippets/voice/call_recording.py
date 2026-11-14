import bostie
from bostie import Context, voice

bot = bostie.Bot()


@bot.command
async def record_call(ctx: Context, minutes: float = 5.0) -> None:
    """Record the whole call as one WAV file, for at most a few minutes."""
    vc = ctx.voice.current or await ctx.voice.connect()
    await ctx.respond("Recording. Everyone in the call can see the bot.")
    recorder = voice.Recorder(
        vc,
        "recordings/{guild_id}/call.wav",
        mix=True,
        max_duration=minutes * 60,
        max_size=200_000_000,
    )
    files = await recorder.wait()
    await ctx.followup(f"Saved {files[None]}; {recorder.stats().lost} packets lost.")
