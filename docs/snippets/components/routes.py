import bostie
from bostie import ButtonStyle, ComponentContext, Context, Range, ui

bot = bostie.Bot()
votes: dict[str, list[int]] = {}


def ballot(topic: str) -> list[ui.TextDict | ui.RowDict]:
    yes, no = votes.setdefault(topic, [0, 0])
    return [
        ui.text(f"**{topic}**: {yes} for, {no} against"),
        ui.row(
            ui.button("For", custom_id=vote.id(side=0, topic=topic)),
            ui.button(
                "Against",
                custom_id=vote.id(side=1, topic=topic),
                style=ButtonStyle.DANGER,
            ),
        ),
    ]


@bot.command
async def propose(ctx: Context, topic: Range[str, 1, 80]) -> None:
    """Start a vote."""
    await ctx.respond(components=ballot(topic))


@bot.on_component("vote:{side:int}:{topic}")
async def vote(ctx: ComponentContext, side: int, topic: str) -> None:
    votes.setdefault(topic, [0, 0])[side] += 1
    await ctx.update(components=ballot(topic))
