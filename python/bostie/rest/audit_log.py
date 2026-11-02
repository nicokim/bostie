from collections.abc import AsyncIterator
from datetime import datetime
from functools import partial
from typing import TypedDict, Unpack

from bostie.enums import AuditLogEvent
from bostie.models import AuditLogPage, decode
from bostie.rest.base import Group, paginate, wire


class AuditLogList(TypedDict, total=False):
    user_id: int
    action_type: AuditLogEvent
    before: int | datetime
    after: int | datetime
    limit: int


class AuditLog(Group):
    def list(
        self, guild_id: int, /, **options: Unpack[AuditLogList]
    ) -> AsyncIterator[AuditLogPage]:
        """Walk the audit log a page at a time, newest first, or oldest with `after`."""
        return paginate(
            self._http,
            "/guilds/{guild_id}/audit-logs",
            wire(options, AuditLogList),
            decode=partial(decode, AuditLogPage),
            items=lambda page: page.audit_log_entries,
            cursor=lambda entry: entry.id,
            page_size=100,
            guild_id=guild_id,
        )
