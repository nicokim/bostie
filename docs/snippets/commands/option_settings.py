from typing import Annotated

import bostie
from bostie import Attachment, ChannelType, Context, Option, PartialChannel, Range

bot = bostie.Bot()


@bot.command
async def announce(
    ctx: Context,
    text: Range[str, 1, 2000],
    target: Annotated[
        PartialChannel,
        Option(
            name="channel",
            description="Where to post it",
            channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_ANNOUNCEMENT],
            description_localizations={"de": "Wo es erscheinen soll"},
        ),
    ],
    delay: Annotated[float, Option(min=0, max=3600, description="Seconds to wait")] = 0,
    image: Annotated[Attachment | None, Option(file_types=["image"])] = None,
) -> None:
    """Post an announcement."""
    extra = f" with {image.filename}" if image is not None else ""
    await ctx.respond(f"Posting in {target.mention} in {delay:g} s{extra}: {text}")
