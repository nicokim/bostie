import asyncio
from datetime import UTC, datetime
from pathlib import Path

import bostie
from bostie import (
    AllowedMentions,
    Context,
    Embed,
    EmbedField,
    EmbedFooter,
    EmbedMedia,
    File,
    PollAnswer,
    PollCreateRequest,
    PollMedia,
    color,
)

bot = bostie.Bot()


@bot.command
async def status(ctx: Context) -> None:
    """Post a status report with a chart."""
    data = await asyncio.to_thread(Path("chart.png").read_bytes)
    chart = File("chart.png", data, description="Latency")
    embed = Embed(
        title="Status",
        description="All systems normal.",
        color=color.GREEN,
        timestamp=datetime.now(UTC),
        fields=(
            EmbedField(name="Gateway", value="41 ms", inline=True),
            EmbedField(name="Voice", value="23 ms", inline=True),
        ),
        image=EmbedMedia(url="attachment://chart.png"),
        footer=EmbedFooter(text="Updated every minute"),
    )
    await ctx.respond(embeds=[embed], files=[chart])


@bot.command
async def lunch(ctx: Context) -> None:
    """Ask where to eat."""
    poll = PollCreateRequest(
        question=PollMedia(text="Where do we eat?"),
        answers=(
            PollAnswer(poll_media=PollMedia(text="Pizza")),
            PollAnswer(poll_media=PollMedia(text="Ramen")),
        ),
        duration=2,
    )
    await ctx.respond(poll=poll)


@bot.command
async def shout(ctx: Context, text: str) -> None:
    """Repeat the text, letting @everyone ping."""
    await ctx.respond(text, allowed_mentions=AllowedMentions(parse=("everyone",)))
