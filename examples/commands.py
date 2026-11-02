"""Slash commands with typed options, a group, autocomplete, context menus and checks.

Names and descriptions are translated from gettext catalogs, such as
locales/fr/LC_MESSAGES/commands.mo next to this file, when there are any.
"""

import gettext
from enum import Enum
from functools import cache
from pathlib import Path
from typing import Annotated, Literal

import bostie
from bostie import (
    AllowedMentions,
    AutocompleteContext,
    Context,
    ErrorContext,
    Forbidden,
    InteractionContextType,
    Locale,
    Message,
    Option,
    Permissions,
    Range,
    ResolvedMember,
    Role,
    User,
    cdn,
    fmt,
    snowflake_time,
    ui,
)
from bostie.checks import Cooldown, bot_has_permissions

LOCALES = Path(__file__).parent / "locales"


@cache
def catalog(locale: Locale) -> gettext.NullTranslations:
    language = locale.value.replace("-", "_")
    return gettext.translation("commands", LOCALES, [language], fallback=True)


def translate(text: str, locale: Locale) -> str | None:
    return catalog(locale).gettext(text)


bot = bostie.Bot(translate=translate)

FRUITS = ("apple", "banana", "cherry", "grape", "lemon", "mango", "orange", "pear")
NO_PINGS = AllowedMentions(parse=())


class Unit(Enum):
    KILOMETRES = "km"
    MILES = "mi"


@bot.command
async def convert(
    ctx: Context,
    kilometres: Annotated[Range[float, 0, 40_000], "How far"],
    unit: Annotated[Unit, "The unit of the answer"] = Unit.MILES,
) -> None:
    """Convert a distance."""
    value = kilometres if unit is Unit.KILOMETRES else kilometres / 1.609344
    await ctx.respond(f"{value:.2f} {unit.value}")


async def fruit_names(ctx: AutocompleteContext, typed: str) -> list[str]:
    return [fruit for fruit in FRUITS if typed.lower() in fruit]


@bot.command(ephemeral=True)
async def pick(
    ctx: Context,
    fruit: Annotated[str, Option(autocomplete=fruit_names)],
    size: Literal["small", "large"] = "small",
) -> None:
    """Pick a fruit; only you see the answer."""
    await ctx.respond(f"A {size} {fruit} for you.")


roles = bot.group(
    "roles",
    "Manage roles",
    default_permissions=Permissions.MANAGE_ROLES,
    contexts=[InteractionContextType.GUILD],
    checks=[bot_has_permissions(Permissions.MANAGE_ROLES)],
)


@roles.command(name="give")
async def roles_give(
    ctx: Context, member: ResolvedMember, role: Role, reason: str | None = None
) -> None:
    """Give a member a role."""
    if ctx.guild_id is not None:
        await bot.rest.members.add_role(
            ctx.guild_id, member.user.id, role.id, reason=reason
        )
        await ctx.respond(
            f"{member.mention} has {role.mention} now.", allowed_mentions=NO_PINGS
        )


@bot.user_command("High five", checks=[Cooldown(1, 30)])
async def high_five(ctx: Context, user: User) -> None:
    await ctx.respond(f"{ctx.user.mention} high-fives {user.mention}!")


@bot.user_command("Profile")
async def profile(ctx: Context, user: User) -> None:
    joined = fmt.timestamp(snowflake_time(user.id), "R")
    await ctx.respond(
        components=[
            ui.section(
                ui.text(f"**{user.display_name}** joined Discord {joined}."),
                accessory=ui.thumbnail(cdn.display_avatar(user, size=128)),
            )
        ],
        ephemeral=True,
    )


@bot.message_command("Quote")
async def quote(ctx: Context, message: Message) -> None:
    await ctx.respond(f"> {message.content}\n{message.author.mention}")


@bot.on_error(Forbidden)
async def missing_permission(ctx: ErrorContext, error: Forbidden) -> None:
    if not isinstance(ctx, AutocompleteContext):
        await ctx.respond("I am missing a permission for that.", ephemeral=True)


if __name__ == "__main__":
    bot.run()
