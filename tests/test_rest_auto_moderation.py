from fakediscord import FakeDiscord, payloads, run, running

from bostie import (
    AutoModerationAction,
    AutoModerationActionMetadata,
    AutoModerationActionType,
    AutoModerationEventType,
    AutoModerationTriggerMetadata,
    AutoModerationTriggerType,
    Bot,
)


def test_a_rule_goes_out_with_only_the_metadata_its_types_use() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        rule = payloads.fixture("events/AUTO_MODERATION_RULE_CREATE")
        async with running(bot, fake):
            fake.fail("POST", "/guilds/1/auto-moderation/rules", 200, rule)
            created = await bot.rest.auto_moderation.create(
                1,
                name="no spam",
                event_type=AutoModerationEventType.MESSAGE_SEND,
                trigger_type=AutoModerationTriggerType.KEYWORD,
                trigger_metadata=AutoModerationTriggerMetadata(
                    keyword_filter=("spam",)
                ),
                actions=[
                    AutoModerationAction(
                        type=AutoModerationActionType.BLOCK_MESSAGE,
                        metadata=AutoModerationActionMetadata(custom_message="no"),
                    ),
                    AutoModerationAction(
                        type=AutoModerationActionType.BLOCK_MEMBER_INTERACTION
                    ),
                ],
            )
            assert fake.requests[-1].json == {
                "name": "no spam",
                "event_type": 1,
                "trigger_type": 1,
                "trigger_metadata": {"keyword_filter": ["spam"]},
                "actions": [
                    {"type": 1, "metadata": {"custom_message": "no"}},
                    {"type": 4},
                ],
            }
            assert created.id == int(rule["id"])

    run(scenario)
