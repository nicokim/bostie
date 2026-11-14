import bostie
from bostie import (
    ApplicationIntegrationType,
    Context,
    InteractionContextType,
    Permissions,
)

bot = bostie.Bot()


@bot.command(
    name="purge",
    description="Delete recent messages",
    name_localizations={"es-ES": "purgar"},
    description_localizations={"es-ES": "Borra mensajes recientes"},
    default_permissions=Permissions.MANAGE_MESSAGES,
    contexts=[InteractionContextType.GUILD],
    integration_types=[ApplicationIntegrationType.GUILD_INSTALL],
    ephemeral=True,
    auto_defer=1.0,
)
async def purge_messages(ctx: Context, count: int = 10) -> None:
    await ctx.respond(f"Deleting {count} messages.")


@bot.command(guilds=[1335172792320131073], nsfw=True)
async def staff(ctx: Context) -> None:
    """Registered in one guild only, and shown in age-restricted channels only."""
    await ctx.respond("Staff only.")
