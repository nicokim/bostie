import logging

import bostie
from bostie.events import VoiceServerUpdate

log = logging.getLogger("my-bot")
bot = bostie.Bot()


async def hand_to_engine(guild_id: int, channel_id: int) -> None:
    external = await bot.voice.connect_external(guild_id, channel_id)
    log.info(
        "session %s on %s",
        external.state.voice_state.session_id,
        external.server.endpoint,
    )
    async with external.updates() as updates:
        async for update in updates:
            if isinstance(update, VoiceServerUpdate):
                log.info("new voice server %s", update.endpoint)
            elif update.voice_state.channel_id is None:
                break
    await external.disconnect()
