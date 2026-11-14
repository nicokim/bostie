from bostie import Bot, ComponentContext, Context, ui
from bostie.events import BotEvent, MessageCreate
from bostie.testing import FakeDiscord, payloads, run, running

INTERACTION_COMMAND = 2
INTERACTION_COMPONENT = 3


class Counted(BotEvent, kw_only=True):
    count: int


def make_bot(api_url: str) -> Bot:
    bot = Bot(api_url=api_url)
    seen: list[str] = []

    @bot.command
    async def hello(ctx: Context) -> None:
        """Say hello."""
        wave = ui.button("Wave", custom_id="wave")
        await ctx.respond("Hello!", components=[ui.row(wave)])

    @bot.on_component("wave")
    async def wave(ctx: ComponentContext) -> None:
        await ctx.update(content="*waves*")

    @bot.listen
    async def count(event: MessageCreate) -> None:
        seen.append(event.message.content)
        await bot.rest.messages.create(event.message.channel_id, f"{len(seen)} seen")
        bot.dispatch(Counted(count=len(seen)))

    return bot


def test_hello_answers_and_the_button_edits_the_message() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with running(make_bot(fake.api_url), fake):
            reply = await fake.interaction(
                INTERACTION_COMMAND, payloads.command("hello")
            )
            update = await fake.interaction(
                INTERACTION_COMPONENT, payloads.button("wave")
            )

        assert reply.json["data"]["content"] == "Hello!"
        assert update.json["data"]["content"] == "*waves*"

    run(scenario)


def test_each_message_is_counted_out_loud() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = make_bot(fake.api_url)
        async with running(bot, fake), bot.stream(Counted) as counted:
            await fake.dispatch("MESSAGE_CREATE", payloads.message(content="hi"))
            first = await anext(counted)

        sent = [r.json for r in fake.requests if r.path.endswith("/messages")]
        assert first.count == 1
        assert [message["content"] for message in sent] == ["1 seen"]

    run(scenario)
