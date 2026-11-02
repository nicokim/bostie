"""A modal opened from a button and read on submit, and one a command waits for."""

import asyncio
from typing import Literal

import bostie
from bostie import ButtonStyle, ComponentContext, Context, ModalContext, ui

bot = bostie.Bot()

TOPICS = ("bug", "idea", "question")


@bot.command
async def feedback(ctx: Context) -> None:
    """Send feedback to the team."""
    button = ui.button(
        "Write feedback", custom_id=open_form.id(), style=ButtonStyle.PRIMARY
    )
    await ctx.respond(components=[ui.row(button)], ephemeral=True)


@bot.on_component("feedback:open")
async def open_form(ctx: ComponentContext) -> None:
    topics = [ui.select_option(topic.title(), topic) for topic in TOPICS]
    await ctx.modal(
        ui.modal(
            submitted.id(),
            "Feedback",
            ui.label("Topic", ui.string_select("topic", topics)),
            ui.label(
                "What should we know?",
                ui.text_input("details", style=ui.TextInputStyle.PARAGRAPH),
            ),
        )
    )


@bot.on_modal("feedback:sent")
async def submitted(ctx: ModalContext) -> None:
    topic = ctx.values("topic")[0]
    await ctx.respond(
        f"Thanks! Filed as a {topic}: {ctx.text('details')}", ephemeral=True
    )


@bot.command
async def ask(ctx: Context, team: Literal["design", "support"]) -> None:
    """Ask a team a question in a form."""
    form = f"ask:{ctx.interaction.id}"
    question = ui.text_input("question", style=ui.TextInputStyle.PARAGRAPH)
    await ctx.modal(ui.modal(form, f"Ask {team}", ui.label("Question", question)))
    try:
        async with asyncio.timeout(600):
            sent = await bot.wait_for_modal(form)
    except TimeoutError:
        return
    await sent.respond(f"Sent to {team}: {sent.text('question')}", ephemeral=True)


if __name__ == "__main__":
    bot.run()
