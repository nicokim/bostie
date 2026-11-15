import bostie
from bostie import Intents

bot = bostie.Bot(intents=Intents.ALL & ~Intents.GUILD_PRESENCES)
