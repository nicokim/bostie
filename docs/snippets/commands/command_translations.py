import bostie
from bostie import Context, Locale

TRANSLATIONS = {
    Locale.FRENCH: {"greet": "saluer", "Greet someone": "Saluer quelqu'un"},
    Locale.GERMAN: {"greet": "grüßen", "Greet someone": "Jemanden grüßen"},
}


def translate(text: str, locale: Locale) -> str | None:
    return TRANSLATIONS.get(locale, {}).get(text)


bot = bostie.Bot(translate=translate)


@bot.command
async def greet(ctx: Context) -> None:
    """Greet someone."""
    await ctx.respond(f"Hello! Your client is set to {ctx.locale.value}.")
