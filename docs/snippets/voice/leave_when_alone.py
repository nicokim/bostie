import bostie
from bostie import voice

bot = bostie.Bot()


@bot.listen
async def leave_when_alone(event: voice.UserLeave) -> None:
    vc = bot.voice.get(event.guild_id)
    if vc is not None and not vc.members:
        await vc.disconnect()
