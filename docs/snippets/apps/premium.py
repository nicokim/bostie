import bostie
from bostie import (
    ApplicationIntegrationType,
    Context,
    InteractionContextType,
    fmt,
    ui,
)
from bostie.events import EntitlementCreate

PREMIUM_SKU = 1088510058284990888
bot = bostie.Bot(
    contexts=[
        InteractionContextType.GUILD,
        InteractionContextType.BOT_DM,
        InteractionContextType.PRIVATE_CHANNEL,
    ],
    integration_types=[
        ApplicationIntegrationType.GUILD_INSTALL,
        ApplicationIntegrationType.USER_INSTALL,
    ],
)


@bot.command
async def forecast(ctx: Context) -> None:
    """A ten-day forecast for premium users."""
    owned = {entitlement.sku_id for entitlement in ctx.interaction.entitlements}
    if PREMIUM_SKU not in owned:
        await ctx.respond(
            components=[
                ui.text(f"Premium unlocks this. {fmt.store(bot.application_id or 0)}"),
                ui.row(ui.premium_button(PREMIUM_SKU)),
            ],
            ephemeral=True,
        )
        return
    await ctx.respond("Sunny for ten days.", ephemeral=True)


@bot.listen
async def bought(event: EntitlementCreate) -> None:
    if event.entitlement.user_id is not None:
        channel = await bot.rest.channels.create_dm(
            recipient_id=event.entitlement.user_id
        )
        await bot.rest.messages.create(channel.id, "Thanks for going premium!")
