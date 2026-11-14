import logging
import time

import bostie
from bostie import (
    AutocompleteContext,
    Context,
    CooldownActive,
    ErrorContext,
    Forbidden,
    NotFound,
)

log = logging.getLogger("my-bot")
bot = bostie.Bot()
started: dict[int, float] = {}


@bot.before_invoke
async def start_timer(ctx: Context) -> None:
    started[ctx.interaction.id] = time.monotonic()


@bot.after_invoke
async def log_duration(ctx: Context) -> None:
    took = time.monotonic() - started.pop(ctx.interaction.id)
    log.info("/%s took %.2f s", ctx.command_name, took)


@bot.command
async def fetch(ctx: Context, message_id: str) -> None:
    """Show a message of this channel."""
    if ctx.channel_id is not None:
        message = await bot.rest.messages.fetch(ctx.channel_id, int(message_id))
        await ctx.respond(message.content or "(no text)")


@fetch.on_error(NotFound, ValueError)
async def no_such_message(ctx: ErrorContext, error: NotFound | ValueError) -> None:
    if not isinstance(ctx, AutocompleteContext):
        await ctx.respond("No message has that id here.", ephemeral=True)


@bot.on_error(Forbidden)
async def missing_access(ctx: ErrorContext, error: Forbidden) -> None:
    if not isinstance(ctx, AutocompleteContext):
        await ctx.respond("I cannot see that channel.", ephemeral=True)


@bot.on_error(CooldownActive)
async def slow_down(ctx: ErrorContext, error: CooldownActive) -> None:
    log.info("%s hit a cooldown, %.0f s left", ctx.user, error.retry_after)
    raise error
