from typing import Annotated

import bostie
from bostie import Attachment, Context, PartialChannel, ResolvedMember, Role

bot = bostie.Bot()


@bot.command
async def report(
    ctx: Context,
    member: Annotated[ResolvedMember, "Who to report"],
    reason: Annotated[str, "What happened"],
    channel: PartialChannel | None = None,
    proof: Attachment | None = None,
    notify: Role | None = None,
    urgent: bool = False,
) -> None:
    """Report a member to the moderators."""
    lines = [f"{member.mention}: {reason}"]
    if channel is not None:
        lines.append(f"In {channel.mention}")
    if proof is not None:
        lines.append(f"Proof: {proof.url}")
    if notify is not None:
        lines.append(f"Notify {notify.mention}")
    if urgent:
        lines.append("Urgent")
    await ctx.respond("\n".join(lines), ephemeral=True)
