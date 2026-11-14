import asyncio

import bostie
from bostie import Context, ui

bot = bostie.Bot()


@bot.command
async def rename(ctx: Context) -> None:
    """Pick a new nickname."""
    form = f"rename:{ctx.interaction.id}"
    field = ui.text_input("nick", min_length=1, max_length=32, value=ctx.user.username)
    await ctx.modal(ui.modal(form, "New nickname", ui.label("Nickname", field)))
    try:
        async with asyncio.timeout(300):
            sent = await bot.wait_for_modal(form)
    except TimeoutError:
        return
    await sent.respond(f"You will be called {sent.text('nick')}.", ephemeral=True)
