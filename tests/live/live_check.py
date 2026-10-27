import asyncio
import logging
import os
import sys
from typing import Annotated, Any

from bostie import (
    AutocompleteContext,
    Bot,
    Choice,
    ComponentContext,
    Context,
    File,
    Option,
    Range,
    ui,
)
from bostie.events import Ready, Resumed

VARIABLES = ("BOSTIE_TOKEN", "BOSTIE_LIVE_GUILD", "BOSTIE_LIVE_CHANNEL")
HUMAN_TIMEOUT = 120
GATEWAY_TIMEOUT = 30
FLAVORS = {
    f"flavor-{name}": name.title()
    for name in ("vanilla", "chocolate", "strawberry", "pistachio")
}

results: list[bool] = []


class SyncLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def report(step: str, passed: bool) -> None:
    results.append(passed)
    print(f"{'PASS' if passed else 'FAIL'}  {step}", flush=True)


def settle(future: asyncio.Future[Any], value: object) -> None:
    if not future.done():
        future.set_result(value)


def check_bot(guild_id: int, seen: dict[str, asyncio.Future[Any]]) -> Bot:
    bot = Bot(dev_guilds=[guild_id])

    async def flavors(ctx: AutocompleteContext, value: str) -> list[Choice]:
        return [
            Choice(name, key)
            for key, name in FLAVORS.items()
            if value.lower() in name.lower()
        ]

    @bot.on_component("bostie-check:{count:int}")
    async def clicked(ctx: ComponentContext, count: int) -> None:
        await ctx.update(components=[ui.text(f"Button route works ({count}).")])
        settle(seen["button"], count)

    @bot.command(name="bostie-check")
    async def check(
        ctx: Context,
        flavor: Annotated[str, Option(autocomplete=flavors)],
        count: Annotated[Range[int, 1, 10], "How many scoops"] = 3,
    ) -> None:
        """Run the bostie live check."""
        button = ui.button("Click me", custom_id=clicked.id(count=count))
        await ctx.respond(components=[ui.text(f"{count} x {flavor}"), ui.row(button)])
        settle(seen["command"], (flavor, count))

    return bot


async def connect(bot: Bot, token: str) -> "asyncio.Task[None]":
    started = asyncio.create_task(bot.start(token))
    ready = asyncio.ensure_future(bot.wait_until_ready())
    await asyncio.wait(
        [started, ready],
        timeout=GATEWAY_TIMEOUT,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if started.done():
        started.result()
    report("connect", ready.done())
    ready.cancel()
    return started


async def live_check(token: str, guild_id: int, channel_id: int) -> None:
    sync_log = SyncLog()
    logging.getLogger("bostie.sync").addHandler(sync_log)
    loop = asyncio.get_running_loop()
    seen = {name: loop.create_future() for name in ("command", "button")}
    bot = check_bot(guild_id, seen)
    readies: list[Ready] = []

    @bot.listen
    async def count_ready(event: Ready) -> None:
        readies.append(event)

    started = await connect(bot, token)
    scope = f"(guild {guild_id})"
    report(
        "sync /bostie-check",
        any(scope in message for message in sync_log.messages),
    )

    print(
        f"\nIn the test guild, within {HUMAN_TIMEOUT} s: type /bostie-check, pick a "
        "flavor from the suggestions, send the command, then click its button.\n",
        flush=True,
    )
    await asyncio.wait(seen.values(), timeout=HUMAN_TIMEOUT)
    answered, clicked = seen.values()
    report("command answered with its typed options", answered.done())
    report(
        "autocomplete answer accepted (the command got a suggestion's value)",
        answered.done() and answered.result()[0] in FLAVORS,
    )
    report(
        "button routed by its custom_id",
        answered.done() and clicked.done() and clicked.result() == answered.result()[1],
    )

    before = len(readies)
    resumed = asyncio.ensure_future(bot.wait_for(Resumed))
    loop.call_soon(bot.gateway.reconnect)
    await asyncio.wait([resumed], timeout=GATEWAY_TIMEOUT)
    report(
        "reconnect resumes without a new READY",
        resumed.done() and len(readies) == before,
    )
    resumed.cancel()

    message = await bot.send_message(
        channel_id,
        components=[ui.text("bostie live check"), ui.file("check.txt")],
        files=[File("check.txt", b"bostie")],
    )
    report(
        "send a Components V2 message with a file",
        [attachment.filename for attachment in message.attachments] == ["check.txt"],
    )

    await bot.close()
    await started
    sync_log.messages.clear()
    again = check_bot(guild_id, seen)
    restarted = await connect(again, token)
    report(
        "a restart with unchanged commands sends no PUT",
        f"commands up to date {scope}" in sync_log.messages,
    )
    await again.close()
    await restarted


def main() -> None:
    missing = [name for name in VARIABLES if not os.environ.get(name)]
    if missing:
        sys.exit(f"the live check needs {', '.join(missing)}; see CONTRIBUTING.md")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        live_check(
            os.environ["BOSTIE_TOKEN"],
            int(os.environ["BOSTIE_LIVE_GUILD"]),
            int(os.environ["BOSTIE_LIVE_CHANNEL"]),
        )
    )
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
