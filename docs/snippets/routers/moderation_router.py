import logging

import bostie
from bostie import (
    ComponentContext,
    Context,
    ErrorContext,
    Forbidden,
    InteractionContextType,
    Permissions,
    ResolvedMember,
    Router,
    ui,
)
from bostie.checks import bot_has_permissions
from bostie.events import BanAdd

log = logging.getLogger("my-bot")
moderation = Router(
    default_permissions=Permissions.BAN_MEMBERS,
    contexts=[InteractionContextType.GUILD],
    ephemeral=True,
)
moderation.check(bot_has_permissions(Permissions.BAN_MEMBERS))


@moderation.command
async def ban(ctx: Context, member: ResolvedMember) -> None:
    """Ban a member after a confirmation."""
    confirm = ui.button("Ban", custom_id=confirmed.id(user_id=member.id))
    await ctx.respond(f"Ban {member.mention}?", components=[ui.row(confirm)])


@moderation.on_component("ban:{user_id:int}")
async def confirmed(ctx: ComponentContext, user_id: int) -> None:
    if ctx.guild_id is not None:
        reason = f"Requested by {ctx.user.username}"
        await moderation.bot.rest.bans.create(ctx.guild_id, user_id, reason=reason)
    await ctx.update(content="Banned.", components=[])


@moderation.listen
async def banned(event: BanAdd) -> None:
    log.info("%s was banned from guild %d", event.user.username, event.guild_id)


@moderation.on_error(Forbidden)
async def cannot_ban(ctx: ErrorContext, error: Forbidden) -> None:
    if isinstance(ctx, Context | ComponentContext):
        await ctx.respond("That member is above the bot.")


bot = bostie.Bot()
bot.include(moderation)
