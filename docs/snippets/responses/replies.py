import bostie
from bostie import Intents, MessageFlags
from bostie.events import MessageCreate

bot = bostie.Bot(intents=Intents.DEFAULT | Intents.MESSAGE_CONTENT)


@bot.listen
async def thank(event: MessageCreate) -> None:
    message = event.message
    if message.author.bot or "thanks" not in message.content.lower():
        return
    await bot.rest.messages.create(
        message.channel_id,
        "You're welcome!",
        reply_to=message.id,
        flags=MessageFlags.SUPPRESS_NOTIFICATIONS,
    )
