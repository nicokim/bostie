"""Components V2 messages, a pager, a select menu and a button that confirms a kick."""

import asyncio

import bostie
from bostie import (
    ButtonStyle,
    ComponentContext,
    Context,
    InteractionContextType,
    Permissions,
    ResolvedMember,
    color,
    ui,
)
from bostie.checks import bot_has_permissions

bot = bostie.Bot()

PAGES = ("Rust runs the gateway.", "Python runs your code.", "Voice runs in Rust too.")
COLORS = {"blurple": color.BLURPLE, "green": color.GREEN, "red": color.parse("#E74C3C")}


def tour_page(number: int) -> list[ui.ContainerDict]:
    return [
        ui.container(
            ui.text("## A short tour"),
            ui.text(PAGES[number]),
            ui.separator(),
            ui.pager(turn, number, len(PAGES)),
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


@bot.command(
    ephemeral=True,
    default_permissions=Permissions.KICK_MEMBERS,
    contexts=[InteractionContextType.GUILD],
    checks=[bot_has_permissions(Permissions.KICK_MEMBERS)],
)
async def kick(ctx: Context, member: ResolvedMember) -> None:
    """Kick a member once you confirm."""
    confirm = f"kick:{ctx.interaction.id}"
    button = ui.button("Kick", custom_id=confirm, style=ButtonStyle.DANGER)
    await ctx.respond(components=[ui.text(f"Kick {member.mention}?"), ui.row(button)])
    try:
        async with asyncio.timeout(30):
            press = await bot.wait_for_component(confirm)
    except TimeoutError:
        await ctx.edit(components=[ui.text("Nobody was kicked.")])
        return
    if ctx.guild_id is not None:
        await bot.rest.members.remove(ctx.guild_id, member.user.id)
    await press.update(components=[ui.text(f"Kicked {member.mention}.")])


if __name__ == "__main__":
    bot.run()
