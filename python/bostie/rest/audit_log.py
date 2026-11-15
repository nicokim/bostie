from collections.abc import AsyncIterator
from datetime import datetime
from typing import TypedDict, Unpack

from bostie.enums import AuditLogEvent
from bostie.models import AuditLogPage
from bostie.rest._base import ResourceGroup, wire


class AuditLogList(TypedDict, total=False):
    user_id: int
    action_type: AuditLogEvent
    before: int | datetime
    after: int | datetime
    limit: int


class AuditLog(ResourceGroup):
    def list(
        self, guild_id: int, /, **options: Unpack[AuditLogList]
    ) -> AsyncIterator[AuditLogPage]:
        """Walk the audit log a page at a time, newest first, or oldest with `after`."""
        return self._paginate(
            "/guilds/{guild_id}/audit-logs",
            wire(options, AuditLogList),
            decodes=AuditLogPage,
            items=lambda page: page.audit_log_entries,
            cursor=lambda entry: entry.id,
            page_size=100,
            guild_id=guild_id,
        )
