from bostie import Bot, File, WebhookMessage
from bostie.testing import FakeDiscord, payloads, run, running

MENTIONS = {"parse": ["users", "roles"]}
ROUTE = "/webhooks/1/secret-token"


def test_a_webhook_waits_for_its_message_and_edits_it_with_nulls() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        sent = payloads.fixture("rest/message") | {"webhook_id": "1"}
        async with running(bot, fake):
            webhooks = bot.rest.webhooks
            fake.fail("POST", ROUTE, 200, sent)
            message = await webhooks.execute(
                1,
                "secret-token",
                "deploy done",
                username="CI",
                thread_id=9,
                files=[File("log.txt", b"ok")],
            )
            fake.fail("PATCH", f"{ROUTE}/messages/{sent['id']}", 200, sent)
            await webhooks.edit_message(
                1, "secret-token", message.id, content=None, embeds=[], thread_id=9
            )
            execute, edit = fake.requests[-2:]
            assert isinstance(message, WebhookMessage)
            assert execute.query == {"wait": "true", "thread_id": "9"}
            assert execute.json == {
                "content": "deploy done",
                "username": "CI",
                "allowed_mentions": MENTIONS,
                "attachments": [{"id": 0, "filename": "log.txt"}],
            }
            assert edit.query == {"thread_id": "9"}
            assert edit.json == {
                "content": None,
                "embeds": [],
                "allowed_mentions": MENTIONS,
            }

    run(scenario)
