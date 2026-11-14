import logging

import bostie
from bostie import Context
from bostie.events import BotEvent, ListenerError, RawEvent

log = logging.getLogger("my-bot")
bot = bostie.Bot()


class LevelUp(BotEvent, kw_only=True):
    user_id: int
    level: int


@bot.command
async def train(ctx: Context) -> None:
    """Gain a level."""
    bot.dispatch(LevelUp(user_id=ctx.user.id, level=2))
    await ctx.respond("You trained hard.")


@bot.listen
async def congratulate(event: LevelUp) -> None:
    log.info("user %d reached level %d", event.user_id, event.level)


@bot.listen_raw("GUILD_AUDIT_LOG_ENTRY_CREATE", "SOME_FUTURE_EVENT")
async def raw(event: RawEvent) -> None:
    log.info("%s, sequence %d: %r", event.name, event.seq, event.raw)


@bot.listen
async def failed(failure: ListenerError) -> None:
    log.error(
        "%s failed on %r", failure.listener, failure.event, exc_info=failure.error
    )
