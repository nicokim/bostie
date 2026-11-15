import os

import bostie
from bostie import AuditLogEvent, Permissions
from bostie.events import AuditLogEntryCreate

ALERTS_CHANNEL_ID = int(os.environ.get("ALERTS_CHANNEL_ID", "0"))

bot = bostie.Bot()


@bot.listen
async def admin_granted(event: AuditLogEntryCreate) -> None:
    entry = event.entry
    if entry.action_type is not AuditLogEvent.ROLE_UPDATE:
        return
    for change in entry.changes:
        before = change.old_value or Permissions(0)
        after = change.new_value or Permissions(0)
        if change.key == "permissions" and after & ~before & Permissions.ADMINISTRATOR:
            await bot.rest.messages.create(
                ALERTS_CHANNEL_ID,
                f"<@{entry.user_id}> gave <@&{entry.target_id}> Administrator.",
            )
