import bostie
from bostie import ComponentContext, Context, ModalContext, ui

bot = bostie.Bot()


@bot.command
async def notifications(ctx: Context) -> None:
    """Change what you are notified about."""
    button = ui.button("Edit", custom_id=edit.id())
    await ctx.respond(components=[ui.text("Notifications: none"), ui.row(button)])


@bot.on_component("notifications:edit")
async def edit(ctx: ComponentContext) -> None:
    topics = [
        ui.group_option("Releases", "releases"),
        ui.group_option("Events", "events", default=True),
        ui.group_option("Polls", "polls"),
    ]
    await ctx.modal(
        ui.modal(
            saved.id(),
            "Notifications",
            ui.label(
                "Topics",
                ui.checkbox_group("topics", topics, min_values=0, required=False),
            ),
            ui.label("Also by DM", ui.checkbox("dm")),
            ui.label("Who may ping you", ui.user_select("pingers", max_values=5)),
        )
    )


@bot.on_modal("notifications:saved")
async def saved(ctx: ModalContext) -> None:
    topics = ", ".join(ctx.values("topics")) or "none"
    by_dm = " by DM too" if ctx.checked("dm") else ""
    pingers = " ".join(user.mention for user in ctx.users("pingers"))
    await ctx.update(
        components=[ui.text(f"Notifications: {topics}{by_dm}. Pings from {pingers}.")]
    )
