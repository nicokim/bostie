"""Record everyone in a voice channel, one Ogg Opus file each, until /stop."""

import bostie
from bostie import Context, InteractionContextType, voice

bot = bostie.Bot()
recorders: dict[int, voice.Recorder] = {}


@bot.command(contexts=[InteractionContextType.GUILD])
async def record(ctx: Context) -> None:
    """Record this voice channel. Tell everyone before you do."""
    vc = ctx.voice.current or await ctx.voice.connect()
    if vc.guild_id not in recorders:
        recorders[vc.guild_id] = voice.Recorder(
            vc, "recordings/{guild_id}/{user_id}.ogg"
        )
    await ctx.respond("Recording. Everyone in this call can see the bot.")


@bot.command(contexts=[InteractionContextType.GUILD])
async def stop(ctx: Context) -> None:
    """Stop recording and list the files."""
    recorder = recorders.pop(ctx.guild_id or 0, None)
    if recorder is None:
        await ctx.respond("Nothing is recording.", ephemeral=True)
        return
    files = await recorder.stop()
    await ctx.respond("\n".join(f"<@{user}>: `{path}`" for user, path in files.items()))


if __name__ == "__main__":
    bot.run()
