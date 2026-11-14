import asyncio

import bostie
from bostie import Context
from bostie.events import ReactionAdd

bot = bostie.Bot()


@bot.command
async def vote(ctx: Context, question: str) -> None:
    """Count thumbs-up reactions for 60 seconds."""
    message = await ctx.respond(question)
    me = bot.user.id if bot.user is not None else 0
    voters: set[int] = set()

    def counts(event: ReactionAdd) -> bool:
        return (
            event.message_id == message.id
            and event.emoji.name == "\N{THUMBS UP SIGN}"
            and event.user_id != me
        )

    async with bot.stream(ReactionAdd, check=counts) as reactions:
        await bot.rest.reactions.create(
            message.channel_id, message.id, "\N{THUMBS UP SIGN}"
        )
        try:
            async with asyncio.timeout(60):
                async for reaction in reactions:
                    voters.add(reaction.user_id)
        except TimeoutError:
            pass
    await ctx.followup(f"{len(voters)} votes.")
