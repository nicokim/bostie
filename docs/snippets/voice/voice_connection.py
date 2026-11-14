import logging

import bostie
from bostie import Context, PartialChannel, User, voice
from bostie.voice import DaveTransition, Moved, StateChange

log = logging.getLogger("my-bot")
bot = bostie.Bot(voice=voice.Defaults(timeout=15.0))


@bot.command
async def join(ctx: Context) -> None:
    """Join your voice channel."""
    vc = ctx.voice.current or await ctx.voice.connect()
    dave = vc.dave
    await ctx.respond(
        f"In <#{vc.channel_id}> with {len(vc.members)} others. "
        f"End-to-end encryption: protocol {dave.protocol_version}, "
        f"privacy code {dave.privacy_code}."
    )


@bot.command
async def verify(ctx: Context, user: User) -> None:
    """Show the code to compare with another user in the call."""
    if (vc := ctx.voice.current) is None:
        await ctx.respond("I am not in a voice channel.", ephemeral=True)
        return
    code = await vc.dave.verification_code(user.id)
    await ctx.respond(f"Compare this with {user.mention}: {code}", ephemeral=True)


@bot.command
async def move(ctx: Context, channel: PartialChannel) -> None:
    """Move the bot to another voice channel."""
    if (vc := ctx.voice.current) is not None:
        await vc.move(channel.id)
        await vc.edit(self_mute=False)
    await ctx.respond("Moved.", ephemeral=True)


@bot.command
async def leave(ctx: Context) -> None:
    """Leave the voice channel."""
    if (vc := ctx.voice.current) is not None:
        await vc.disconnect()
    await ctx.respond("Left.", ephemeral=True)


@bot.listen
async def voice_state(event: StateChange) -> None:
    log.info(
        "guild %d: %s -> %s (%s)", event.guild_id, event.old, event.new, event.detail
    )


@bot.listen
async def moved(event: Moved) -> None:
    who = "we" if event.by_us else "a moderator"
    log.info("%s moved the bot to %d", who, event.new_channel_id)


@bot.listen
async def dave(event: DaveTransition) -> None:
    log.info("DAVE protocol %d, epoch %s", event.protocol_version, event.epoch)
