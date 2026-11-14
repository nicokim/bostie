import bostie
from bostie import ChannelType, ComponentContext, Context, ui

bot = bostie.Bot()

LANGUAGES = {"py": "Python", "rs": "Rust", "ts": "TypeScript"}


@bot.command
async def setup(ctx: Context) -> None:
    """Pick your languages and a channel for news."""
    languages = [ui.select_option(name, code) for code, name in LANGUAGES.items()]
    await ctx.respond(
        components=[
            ui.row(
                ui.string_select(
                    "setup:languages",
                    languages,
                    placeholder="Languages you use",
                    min_values=1,
                    max_values=len(languages),
                )
            ),
            ui.row(
                ui.channel_select(
                    "setup:news",
                    channel_types=[ChannelType.GUILD_TEXT],
                    placeholder="Where to post news",
                )
            ),
            ui.row(ui.role_select("setup:roles", max_values=3)),
        ],
        ephemeral=True,
    )


@bot.on_component("setup:languages")
async def languages_picked(ctx: ComponentContext) -> None:
    names = ", ".join(LANGUAGES[code] for code in ctx.values)
    await ctx.respond(f"You use {names}.", ephemeral=True)


@bot.on_component("setup:news")
async def news_picked(ctx: ComponentContext) -> None:
    await ctx.respond(f"News goes to {ctx.channels[0].mention}.", ephemeral=True)


@bot.on_component("setup:roles")
async def roles_picked(ctx: ComponentContext) -> None:
    picked = " ".join(role.mention for role in ctx.roles) or "no roles"
    await ctx.respond(f"You picked {picked}.", ephemeral=True)
