import asyncio

import bostie
from bostie import ButtonStyle, Context, ui

bot = bostie.Bot()


@bot.command
async def reset(ctx: Context) -> None:
    """Reset your settings after a confirmation."""
    yes = f"reset:{ctx.interaction.id}"
    button = ui.button("Reset", custom_id=yes, style=ButtonStyle.DANGER)
    await ctx.respond("Reset all your settings?", components=[ui.row(button)])
    try:
        async with asyncio.timeout(30):
            press = await bot.wait_for_component(
                yes, check=lambda press: press.user.id == ctx.user.id
            )
    except TimeoutError:
        await ctx.edit(content="Nothing was reset.", components=[])
        return
    await press.update(content="Your settings are reset.", components=[])
