import logging

import bostie
from bostie import voice

log = logging.getLogger("my-bot")
bot = bostie.Bot(voice=voice.Defaults(stall_after=1.0))


@bot.listen
async def media_flow(event: voice.MediaStalled | voice.MediaResumed) -> None:
    change = "stalled" if isinstance(event, voice.MediaStalled) else "resumed"
    log.warning(
        "guild %d: %s %s after %.1f s",
        event.guild_id,
        event.direction,
        change,
        event.duration,
    )
