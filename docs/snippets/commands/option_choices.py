from enum import Enum
from typing import Annotated, Literal

import bostie
from bostie import Choice, Context, Option

bot = bostie.Bot()


class Size(Enum):
    SMALL = 1
    MEDIUM = 2
    LARGE = 3


@bot.command
async def order(
    ctx: Context,
    drink: Literal["tea", "coffee", "water"],
    size: Size = Size.MEDIUM,
    milk: Annotated[
        Literal["none", "oat", "cow"],
        Option(
            choices=[
                Choice("No milk", "none"),
                Choice("Oat milk", "oat", name_localizations={"fr": "Lait d'avoine"}),
                Choice("Cow milk", "cow"),
            ]
        ),
    ] = "none",
) -> None:
    """Order a drink."""
    await ctx.respond(f"One {size.name.lower()} {drink}, milk: {milk}.")
