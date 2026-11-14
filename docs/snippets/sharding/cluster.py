import asyncio
import logging
import os

import bostie
from bostie.events import ShardDisconnect, ShardReady

log = logging.getLogger("my-bot")
cluster = int(os.environ.get("CLUSTER", "0"))
identify_lock = asyncio.Lock()


async def wait_for_turn(shard_id: int) -> None:
    async with identify_lock:
        await asyncio.sleep(5)


bot = bostie.Bot(
    shard_count=16,
    shard_ids=range(cluster * 4, cluster * 4 + 4),
    before_identify=wait_for_turn,
    session_file=f"sessions-{cluster}.json",
)


@bot.listen
async def shard_ready(event: ShardReady) -> None:
    log.info("shard %d is ready", event.shard_id)


@bot.listen
async def shard_lost(event: ShardDisconnect) -> None:
    action = "resuming" if event.resuming else "starting a new session"
    log.info("shard %d disconnected (%s), %s", event.shard_id, event.code, action)


bot.run()
