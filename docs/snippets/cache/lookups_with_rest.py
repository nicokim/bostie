import bostie
from bostie.events import ReactionAdd

bot = bostie.Bot(max_messages=1000)
STAR = "\N{WHITE MEDIUM STAR}"


@bot.listen
async def starred(event: ReactionAdd) -> None:
    if event.emoji.name != STAR:
        return
    message = await bot.lookup.message(event.channel_id, event.message_id)
    user = await bot.lookup.user(event.user_id)
    await bot.rest.messages.create(
        event.channel_id,
        f"{user.username} starred a message by {message.author.username}.",
        reply_to=message.id,
    )
