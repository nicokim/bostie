"""Listeners, waiting for an event, and events of your own.

/guess reads messages, so turn on the Message Content intent in the Developer Portal.
"""

import asyncio
import logging

import bostie
from bostie import AllowedMentions, Context, Intents, activity
from bostie.events import (
    BotEvent,
    GuildCreate,
    ListenerError,
    MessageCreate,
    ReactionAdd,
    ShardReady,
    Started,
)

log = logging.getLogger("events-example")
bot = bostie.Bot(intents=Intents.DEFAULT | Intents.MESSAGE_CONTENT)


class Milestone(BotEvent, kw_only=True):
    guild_id: int
    count: int


@bot.listen
async def ready(event: ShardReady | Started) -> None:
    log.info("ready: %r", event)


@bot.listen
async def joined(event: GuildCreate) -> None:
    if event.joined:
        log.info("added to guild %d", event.guild.id)


@bot.listen
async def started(event: Started) -> None:
    await bot.update_presence(activities=[activity.custom("Counting reactions")])


reactions: dict[int, int] = {}


@bot.listen
async def count(event: ReactionAdd) -> None:
    if event.guild_id is not None:
        reactions[event.guild_id] = reactions.get(event.guild_id, 0) + 1
        if reactions[event.guild_id] % 100 == 0:
            bot.dispatch(
                Milestone(guild_id=event.guild_id, count=reactions[event.guild_id])
            )


@bot.listen
async def celebrate(event: Milestone) -> None:
    log.info("guild %d reached %d reactions", event.guild_id, event.count)


@bot.listen
async def report(failure: ListenerError) -> None:
    log.error(
        "%s failed on %r", failure.listener, failure.event, exc_info=failure.error
    )


@bot.command
async def guess(ctx: Context) -> None:
    """Guess a number from 1 to 10 in this channel within 30 s."""
    await ctx.respond("I picked a number from 1 to 10. Type your guess!")

    def is_guess(event: MessageCreate) -> bool:
        message = event.message
        return message.channel_id == ctx.channel_id and message.content.isdigit()

    try:
        async with asyncio.timeout(30):
            event = await bot.wait_for(MessageCreate, check=is_guess)
    except TimeoutError:
        await ctx.followup("Time is up: it was 7.")
        return
    verdict = "right" if event.message.content == "7" else "wrong, it was 7"
    await ctx.followup(
        f"{event.message.author.mention} is {verdict}.",
        allowed_mentions=AllowedMentions(parse=()),
    )


if __name__ == "__main__":
    bot.run()
