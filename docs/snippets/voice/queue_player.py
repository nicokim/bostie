import bostie
from bostie import Context, voice

bot = bostie.Bot(voice=voice.Defaults(crossfade=3.0, history=20))
queues: dict[int, voice.Queue] = {}


async def queue_of(ctx: Context) -> voice.Queue:
    vc = ctx.voice.current or await ctx.voice.connect()
    queue = queues.get(vc.guild_id)
    if queue is None or queue.closed:
        queue = queues[vc.guild_id] = voice.Queue(vc)
    return queue


@bot.command
async def add(ctx: Context, url: str, next_up: bool = False) -> None:
    """Add a URL to the queue, or play it next."""
    queue = await queue_of(ctx)
    queue.add(voice.Url(url), meta=ctx.user.id, index=0 if next_up else None)
    await ctx.respond(f"{len(queue.upcoming)} tracks up next.")


@bot.command
async def queue(ctx: Context) -> None:
    """Show what plays now and next."""
    player = await queue_of(ctx)
    lines = [f"Now: {player.now.source!r}" if player.now else "Nothing playing"]
    lines += [f"{i}. {track.source!r}" for i, track in enumerate(player.upcoming, 1)]
    await ctx.respond("\n".join(lines))


@bot.command
async def back(ctx: Context) -> None:
    """Play the previous track again."""
    player = await queue_of(ctx)
    previous = player.back()
    await ctx.respond("Going back." if previous else "Nothing played before.")


@bot.command
async def shuffle(ctx: Context) -> None:
    """Shuffle the upcoming tracks."""
    (await queue_of(ctx)).shuffle()
    await ctx.respond("Shuffled.")
