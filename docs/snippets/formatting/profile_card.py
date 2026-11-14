from datetime import UTC, datetime, timedelta

import bostie
from bostie import AllowedMentions, Context, Embed, EmbedMedia, User, cdn, color, fmt

bot = bostie.Bot()


@bot.user_command("Profile card")
async def profile_card(ctx: Context, user: User) -> None:
    created = bostie.snowflake_time(user.id)
    banner = None
    if user.banner is not None:
        banner = EmbedMedia(url=cdn.user_banner(user.id, user.banner, size=1024))
    embed = Embed(
        title=fmt.escape_markdown(user.display_name),
        description=f"Joined Discord {fmt.timestamp(created, 'R')}",
        color=color.hsv(user.id % 360 / 360, 0.6, 0.9),
        thumbnail=EmbedMedia(url=cdn.display_avatar(user, size=256)),
        image=banner,
    )
    await ctx.respond(embeds=[embed], ephemeral=True)


@bot.command
async def remind(ctx: Context, text: str, minutes: int = 10) -> None:
    """Show a reminder time in everyone's own time zone."""
    due = datetime.now(UTC) + timedelta(minutes=minutes)
    safe = fmt.escape_mentions(text)
    await ctx.respond(
        f"{fmt.user(ctx.user.id)}: {safe} at {fmt.timestamp(due, 't')}",
        allowed_mentions=AllowedMentions(users=(ctx.user.id,)),
    )
