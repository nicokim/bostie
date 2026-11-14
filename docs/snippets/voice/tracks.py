import bostie
from bostie import Context, voice

bot = bostie.Bot()
tracks: dict[int, voice.Track] = {}


@bot.command
async def play(ctx: Context, url: str) -> None:
    """Play a media URL."""
    vc = ctx.voice.current or await ctx.voice.connect()
    track = await vc.play(voice.Url(url), volume=0.8, meta=ctx.user.id)
    tracks[vc.guild_id] = track
    await ctx.respond(f"Playing {track.source.tags.title or url}.")
    ended = await track.wait()
    if ended.reason is voice.EndReason.ERROR:
        await ctx.followup(f"The track failed: {ended.error}")


@bot.command
async def pause(ctx: Context) -> None:
    """Pause or resume the track."""
    track = tracks.get(ctx.guild_id or 0)
    if track is None or track.ended is not None:
        await ctx.respond("Nothing is playing.", ephemeral=True)
        return
    if track.paused:
        track.resume()
    else:
        track.pause()
    await ctx.respond(f"At {track.position:.0f} s.")


@bot.command
async def seek(ctx: Context, seconds: float) -> None:
    """Jump to a point in the track."""
    track = tracks.get(ctx.guild_id or 0)
    if track is not None and track.source.seekable:
        await track.seek(seconds)
    await ctx.respond(f"Now at {seconds:.0f} s.")


@bot.command
async def bass(ctx: Context, gain: float = 6.0) -> None:
    """Boost the bass and slow down a little."""
    track = tracks.get(ctx.guild_id or 0)
    if track is not None:
        track.filters = voice.Filters(bass=gain, speed=0.9)
    await ctx.respond("Changed the filters.")
