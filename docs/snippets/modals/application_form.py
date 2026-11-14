import bostie
from bostie import Context, ModalContext, TextInputStyle, ui

bot = bostie.Bot()


@bot.command
async def apply(ctx: Context) -> None:
    """Apply to join the staff."""
    await ctx.modal(
        ui.modal(
            submitted.id(),
            "Staff application",
            ui.text("Answers are seen by the admins only."),
            ui.label("Why do you want to join?", ui.text_input("why")),
            ui.label(
                "Tell us about yourself",
                ui.text_input("about", style=TextInputStyle.PARAGRAPH, max_length=1000),
                description="Experience, time zone, languages",
            ),
            ui.label(
                "Role",
                ui.radio_group(
                    "role",
                    [
                        ui.group_option("Moderator", "mod"),
                        ui.group_option("Event host", "events"),
                    ],
                ),
            ),
            ui.label("CV or portfolio", ui.file_upload("cv", required=False)),
        )
    )


@bot.on_modal("staff:apply")
async def submitted(ctx: ModalContext) -> None:
    files = ctx.attachments("cv")
    await ctx.respond(
        f"Thanks! You applied as {ctx.choice('role')} because: {ctx.text('why')}"
        f" ({len(files)} file(s) attached)",
        ephemeral=True,
    )
