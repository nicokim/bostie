"""Components V2 messages, a stateless pager and a select menu."""

import bostie
from bostie import ComponentContext, Context, color, ui

bot = bostie.Bot()

PAGES = ("Rust runs the gateway.", "Python runs your code.", "Voice runs in Rust too.")
COLORS = {"blurple": color.BLURPLE, "green": color.GREEN, "red": color.parse("#E74C3C")}


def tour_page(number: int) -> list[ui.ContainerDict]:
    buttons = ui.row(
        ui.button("Back", custom_id=turn.id(page=number - 1), disabled=number == 0),
        ui.button(
            "Next",
            custom_id=turn.id(page=number + 1),
            disabled=number == len(PAGES) - 1,
        ),
    )
    return [
        ui.container(
            ui.text(f"## Page {number + 1} of {len(PAGES)}"),
            ui.text(PAGES[number]),
            ui.separator(),
            buttons,
        )
    ]


@bot.command
async def tour(ctx: Context) -> None:
    """Page through a short tour."""
    await ctx.respond(components=tour_page(0))


@bot.on_component("tour:{page:int}")
async def turn(ctx: ComponentContext, page: int) -> None:
    await ctx.update(components=tour_page(page))


@bot.command
async def paint(ctx: Context) -> None:
    """Choose a color."""
    options = [ui.select_option(name.title(), name) for name in COLORS]
    await ctx.respond(
        components=[
            ui.text("Which color?"),
            ui.row(ui.string_select(chosen.id(), options, placeholder="Pick one")),
        ]
    )


@bot.on_component("paint")
async def chosen(ctx: ComponentContext) -> None:
    color = ctx.values[0]
    await ctx.update(
        components=[
            ui.container(ui.text(f"You chose {color}."), accent_color=COLORS[color])
        ]
    )


if __name__ == "__main__":
    bot.run()
