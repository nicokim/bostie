from dataclasses import dataclass
from typing import Annotated

import bostie
from bostie import Context, Convert, Range

bot = bostie.Bot()


@dataclass(frozen=True)
class Color:
    red: int
    green: int
    blue: int


def parse_color(ctx: Context, text: Range[str, 7, 7]) -> Color:
    value = int(text.removeprefix("#"), 16)
    return Color(value >> 16, value >> 8 & 0xFF, value & 0xFF)


@bot.command
async def paint(
    ctx: Context,
    color: Annotated[Color, Convert(parse_color), "A hex color such as #5865F2"],
) -> None:
    """Paint with a color."""
    await ctx.respond(f"Red {color.red}, green {color.green}, blue {color.blue}.")
