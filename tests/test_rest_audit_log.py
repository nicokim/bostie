from datetime import UTC, datetime
from typing import Any

import msgspec

from bostie import (
    AuditLogEvent,
    AuditLogRole,
    Bot,
    ChannelType,
    CommandPermission,
    CommandPermissionType,
    Overwrite,
    Permissions,
    RoleColors,
)
from bostie.testing import FakeDiscord, payloads, run, running

COLORS = {"primary_color": 0xFF0000, "secondary_color": None, "tertiary_color": None}
OVERWRITE = {"id": "5", "type": 0, "allow": "1024", "deny": "0"}
CHANGES: list[tuple[AuditLogEvent, str, Any, Any]] = [
    (AuditLogEvent.ROLE_UPDATE, "permissions", "8", Permissions.ADMINISTRATOR),
    (
        AuditLogEvent.ROLE_UPDATE,
        "colors",
        COLORS,
        RoleColors(primary_color=0xFF0000, secondary_color=None, tertiary_color=None),
    ),
    (AuditLogEvent.ROLE_UPDATE, "colors", "red", "red"),
    (AuditLogEvent.CHANNEL_OVERWRITE_UPDATE, "allow", "1024", Permissions(1024)),
    (AuditLogEvent.CHANNEL_UPDATE, "type", 0, ChannelType.GUILD_TEXT),
    (
        AuditLogEvent.CHANNEL_CREATE,
        "permission_overwrites",
        [OVERWRITE],
        (msgspec.convert(OVERWRITE, Overwrite, strict=False),),
    ),
    (AuditLogEvent.GUILD_UPDATE, "afk_channel_id", "7", 7),
    (AuditLogEvent.GUILD_UPDATE, "icon_hash", "abc", "abc"),
    (
        AuditLogEvent.MEMBER_UPDATE,
        "communication_disabled_until",
        "2026-11-20T00:00:00+00:00",
        datetime(2026, 11, 20, tzinfo=UTC),
    ),
    (
        AuditLogEvent.MEMBER_ROLE_UPDATE,
        "$add",
        [{"id": "9", "name": "Mod"}],
        (AuditLogRole(id=9, name="Mod"),),
    ),
    (AuditLogEvent.INVITE_CREATE, "channel_id", "7", 7),
    (AuditLogEvent.WEBHOOK_UPDATE, "avatar_hash", "abc", "abc"),
    (AuditLogEvent.THREAD_UPDATE, "archived", True, True),
    (
        AuditLogEvent.APPLICATION_COMMAND_PERMISSION_UPDATE,
        "9",
        {"id": "9", "type": 1, "permission": True},
        CommandPermission(id=9, type=CommandPermissionType.ROLE, permission=True),
    ),
    (AuditLogEvent(999), "anything", {"a": "1"}, {"a": "1"}),
]


def page(ids: range) -> dict[str, Any]:
    entry = payloads.fixture("rest/audit_log_page")["audit_log_entries"][0]
    return payloads.fixture("rest/audit_log_page") | {
        "audit_log_entries": [entry | {"id": str(id)} for id in ids]
    }


def test_the_audit_log_yields_whole_pages_up_to_its_limit_of_entries() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            for ids in (range(300, 200, -1), range(200, 150, -1)):
                fake.fail("GET", "/guilds/1/audit-logs", 200, page(ids))
            pages = bot.rest.audit_log.list(
                1, action_type=AuditLogEvent.MEMBER_KICK, limit=150
            )
            walked = [page async for page in pages]
            assert [len(page.audit_log_entries) for page in walked] == [100, 50]
            assert walked[0].users[0].id == int(payloads.user()["id"])
            assert walked[0].webhooks[0].name == "test webhook"
            assert [request.query for request in fake.requests[-2:]] == [
                {"action_type": "20", "limit": "100"},
                {"action_type": "20", "limit": "50", "before": "201"},
            ]

    run(scenario)


def test_change_values_take_the_type_of_the_changed_objects_field() -> None:
    changed = page(range(len(CHANGES)))
    for entry, (action, key, sent, _) in zip(
        changed["audit_log_entries"], CHANGES, strict=True
    ):
        entry["action_type"] = int(action)
        entry["changes"] = [{"key": key, "new_value": sent, "old_value": sent}]

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            fake.fail("GET", "/guilds/1/audit-logs", 200, changed)
            [decoded] = [page async for page in bot.rest.audit_log.list(1)]

        changes = [entry.changes[0] for entry in decoded.audit_log_entries]
        assert [(change.new_value, change.old_value) for change in changes] == [
            (expected, expected) for *_, expected in CHANGES
        ]

    run(scenario)
