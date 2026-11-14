import msgspec

import bostie
from bostie import (
    Context,
    Embed,
    EmbedField,
    Message,
    MessageType,
    WebhookMessage,
    fmt,
    snowflake_time,
)

bot = bostie.Bot()


@bot.message_command("Inspect")
async def inspect(ctx: Context, message: Message | WebhookMessage) -> None:
    if isinstance(message, WebhookMessage):
        sender = f"the webhook {message.author.username}"
    else:
        sender = message.author.mention
    kind = message.type.name
    if message.type.name.startswith("UNKNOWN_"):
        kind = f"a type bostie does not know yet ({message.type.value})"
    embed = Embed(
        title="Message",
        url=message.jump_url,
        fields=(
            EmbedField(name="Sent by", value=sender),
            EmbedField(name="Type", value=kind),
            EmbedField(name="Created", value=fmt.timestamp(snowflake_time(message.id))),
            EmbedField(name="Raw keys", value=", ".join(sorted(message.raw or {}))),
        ),
    )
    if message.type is MessageType.REPLY:
        embed = msgspec.structs.replace(embed, description="A reply.")
    await ctx.respond(embeds=[embed], ephemeral=True)
