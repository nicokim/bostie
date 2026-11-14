import asyncio
from pathlib import Path

import bostie
from bostie import ButtonStyle, Context, File, color, ui

bot = bostie.Bot()


@bot.command
async def release(ctx: Context) -> None:
    """Show the release notes."""
    notes = await asyncio.to_thread(Path("notes.pdf").read_bytes)
    await ctx.respond(
        components=[
            ui.container(
                ui.section(
                    ui.text("## Version 2.0"),
                    ui.text("Faster startup and a new voice mixer."),
                    accessory=ui.thumbnail("https://example.com/logo.png"),
                ),
                ui.separator(spacing=2),
                ui.media_gallery(
                    ui.gallery_item("https://example.com/before.png"),
                    ui.gallery_item("https://example.com/after.png", spoiler=True),
                ),
                ui.file("notes.pdf"),
                ui.row(
                    ui.link_button("Changelog", "https://example.com/changelog"),
                    ui.button(
                        "Subscribe", custom_id="subscribe", style=ButtonStyle.SUCCESS
                    ),
                ),
                accent_color=color.BLURPLE,
            )
        ],
        files=[File("notes.pdf", notes)],
    )
