from typing import Any

from fakediscord import FakeDiscord, payloads, run, running

from bostie import AuditLogEvent, Bot


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
