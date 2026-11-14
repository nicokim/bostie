import logging

import bostie
from bostie import Intents
from bostie.events import (
    MemberJoin,
    MemberRemove,
    MessageDelete,
    MessageUpdate,
    RoleUpdate,
)

log = logging.getLogger("my-bot")
bot = bostie.Bot(intents=Intents.DEFAULT | Intents.GUILD_MEMBERS, max_messages=1000)


@bot.listen
async def membership(event: MemberJoin | MemberRemove) -> None:
    if isinstance(event, MemberJoin):
        log.info("%s joined guild %d", event.member.user.username, event.guild_id)
    else:
        log.info("%s left guild %d", event.user.username, event.guild_id)


@bot.listen
async def role_renamed(event: RoleUpdate) -> None:
    if event.old is not None and event.old.name != event.role.name:
        log.info("role %r is now %r", event.old.name, event.role.name)


@bot.listen(MessageUpdate)
async def edited(event: MessageUpdate) -> None:
    if event.old is not None:
        log.info("edited: %r -> %r", event.old.content, event.message.content)


@bot.listen
async def deleted(event: MessageDelete) -> None:
    if event.message is not None:
        log.info("deleted: %r", event.message.content)
