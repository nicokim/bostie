from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import bostie
from bostie import ComponentContext, Context, Forbidden, Intents, ResolvedMember, ui
from bostie.events import MessageCreate

bot = bostie.Bot(intents=Intents.DEFAULT | Intents.MESSAGE_CONTENT)
kudos: dict[int, int] = {}


@bot.command
async def thank(
    ctx: Context,
    member: Annotated[ResolvedMember, "Who to thank"],
    size: Literal["small", "big"] = "small",
) -> None:
    """Give a member kudos."""
    kudos[member.id] = kudos.get(member.id, 0) + (5 if size == "big" else 1)
    undo = ui.button("Undo", custom_id=undo_thanks.id(user_id=member.id))
    await ctx.respond(
        f"{member.mention} has {kudos[member.id]} kudos.", components=[ui.row(undo)]
    )


@bot.on_component("thank:undo:{user_id:int}")
async def undo_thanks(ctx: ComponentContext, user_id: int) -> None:
    kudos[user_id] = max(kudos.get(user_id, 0) - 1, 0)
    await ctx.update(
        content=f"Undone: <@{user_id}> has {kudos[user_id]}.", components=[]
    )


@bot.command
async def busy(ctx: Context) -> None:
    """Count the messages sent here in the last hour."""
    if ctx.channel_id is None:
        await ctx.respond("Use this in a channel.", ephemeral=True)
        return
    await ctx.defer(ephemeral=True)
    since = datetime.now(UTC) - timedelta(hours=1)
    count = 0
    try:
        async for _ in bot.rest.messages.list(ctx.channel_id, after=since, limit=500):
            count += 1
    except Forbidden:
        await ctx.respond("I cannot read this channel.")
        return
    await ctx.respond(f"{count} messages in the last hour.")


@bot.listen
async def ping(event: MessageCreate) -> None:
    message = event.message
    if not message.author.bot and message.content == "!ping":
        await bot.rest.messages.create(message.channel_id, "pong", reply_to=message.id)


if __name__ == "__main__":
    bot.run()
