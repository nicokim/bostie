from typing import Annotated

import bostie
from bostie import AutocompleteContext, Choice, Context, Option

bot = bostie.Bot()

CITIES = {
    "fr": ["Paris", "Lyon", "Marseille"],
    "jp": ["Tokyo", "Osaka", "Kyoto"],
}


async def countries(ctx: AutocompleteContext, typed: str) -> list[Choice]:
    return [
        Choice(code.upper(), code) for code in CITIES if code.startswith(typed.lower())
    ]


async def cities(ctx: AutocompleteContext, typed: str) -> list[str]:
    country = ctx.options.get("country", "")
    return [city for city in CITIES.get(country, []) if typed.lower() in city.lower()]


@bot.command
async def weather(
    ctx: Context,
    country: Annotated[str, Option(autocomplete=countries)],
    city: Annotated[str, Option(autocomplete=cities)],
) -> None:
    """Show the weather of a city."""
    await ctx.respond(f"Sunny in {city}, {country.upper()}.")
