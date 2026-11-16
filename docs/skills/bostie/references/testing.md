# Testing a bostie bot

`bostie.testing` runs a fake Discord on localhost: a gateway and the REST routes a bot uses most. The test starts the real bot against it, plays Discord's side, and checks what the bot sent. Nothing reaches the network. It needs the `testing` extra: `pip install "bostie[testing]"`, or the same extra in the project's dependencies.

```python
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
```

- `run(scenario)` starts a `FakeDiscord`, runs `async def scenario(fake)` within 10 seconds and shuts it down. It is a plain function, so pytest needs no plugin and no async marker.
- `async with running(bot, fake):` starts the bot on the fake and waits until it is ready.
- Point the bot at the fake: `Bot(api_url=fake.api_url)` when the test builds the bot, as `make_bot` does. When the bot is built at module level in the project, import it and call `monkeypatch.setenv("BOSTIE_API_URL", fake.api_url)` inside the scenario, before `running`.
- `await fake.interaction(type, data)` sends an interaction and returns the bot's first answer; its `json` is the callback body, so the message is in `reply.json["data"]`. The types are Discord's numbers: 2 for a command, 3 for a component, 4 for autocomplete, 5 for a modal submit. It raises `TimeoutError` when the bot sends nothing within the 3 seconds Discord allows. Interactions built by `payloads` come from the guild of `payloads.voice_channel()`; for a command registered with `guilds=[...]` in another guild, pass that guild as `fake.interaction(2, data, guild_id=str(guild_id))`. When the same name is also registered globally, `payloads.command("name", guild_id=...)` picks the guild's command.
- `bostie.testing.payloads` builds the data: `payloads.command("name", payloads.option("count", 4, 3))` with Discord's option type numbers, `payloads.button("custom_id")`, `payloads.select("custom_id", "value")`, `payloads.modal("custom_id")`, and `payloads.message(content="hi")` for events.
- `await fake.dispatch("MESSAGE_CREATE", payloads.message(content="hi"))` sends a gateway event. To know when a listener is done, have it dispatch an event of its own and wait for that with `bot.stream`, as the second test does; never sleep.
- `fake.requests` lists every REST request the bot made, each with `method`, `path` and `json`. `fake.fail("POST", "/channels", 403, {"code": 50013, "message": "Missing Permissions"})` makes the next matching request fail.
- `FakeDiscord(voice=True)` adds a voice channel and a voice server, so `ctx.voice.connect()` and `vc.play(...)` run in tests; `await fake.voice.wait_for_packets(count)` returns what the bot sent. `run(scenario, voice=True)` passes the option on.
- `await fake.move_user(guild_id, user_id, channel_id)` puts a user in a voice channel as Discord reports it, so `ctx.voice.connect()` joins that channel; `None` as the channel takes the user out. It works before or after the bot starts. The user who runs a command built by `payloads` is `payloads.fixture("events/INTERACTION_CREATE.command")["member"]["user"]["id"]`, in the guild of `payloads.voice_channel()`. Do not write to `fake.voice_states` by hand.
- Do not replace bostie with mocks: the fake checks the requests the bot really makes.
