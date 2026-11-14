import bostie
from bostie import ComponentContext, Context, ui, voice

bot = bostie.Bot()


@bot.command
async def play(ctx: Context, url: str) -> None:
    """Play a direct media URL in your voice channel."""
    vc = ctx.voice.current or await ctx.voice.connect()
    await vc.play(voice.Url(url))
    stop = ui.button("Stop", custom_id=stop_playing.id(guild_id=vc.guild_id))
    await ctx.respond(components=[ui.text(f"Playing <{url}>"), ui.row(stop)])


@bot.on_component("stop:{guild_id:int}")
async def stop_playing(ctx: ComponentContext, guild_id: int) -> None:
    if (vc := bot.voice.get(guild_id)) is not None:
        vc.stop()
    await ctx.update(components=[ui.text("Stopped.")])


bot.run()
