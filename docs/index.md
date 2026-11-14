# bostie

--8<-- "README.md:intro"

## A first bot

```python
from enum import Enum
from typing import Annotated, Literal

import bostie
from bostie import ComponentContext, Context, ModalContext, Range, User, ui
from bostie.events import MessageCreate

bot = bostie.Bot(dev_guilds=[123456789012345678])


class Unit(Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"


@bot.command
async def roll(
    ctx: Context,
    sides: Annotated[Range[int, 2, 100], "Sides per die"] = 6,
    mode: Literal["sum", "list"] = "sum",
    unit: Unit = Unit.METRIC,
) -> None:
    """Roll some dice."""
    again = ui.button("Again", custom_id=reroll.id(sides=sides))
    await ctx.respond(
        components=[ui.text(f"d{sides} in {mode} mode ({unit.value})"), ui.row(again)]
    )


@bot.on_component("reroll:{sides:int}")
async def reroll(ctx: ComponentContext, sides: int) -> None:
    await ctx.update(components=[ui.text(f"Rolled a d{sides} again")])


@bot.user_command("Report")
async def report(ctx: Context, user: User) -> None:
    reason = ui.text_input("reason", style=ui.TextInputStyle.PARAGRAPH)
    await ctx.modal(
        ui.modal(
            reported.id(user=user.id),
            f"Report {user.display_name}",
            ui.label("What happened?", reason),
        )
    )


@bot.on_modal("report:{user:int}")
async def reported(ctx: ModalContext, user: int) -> None:
    await ctx.respond(f"Reported <@{user}>: {ctx.text('reason')}", ephemeral=True)


@bot.listen
async def wave_back(event: MessageCreate) -> None:
    message = event.message
    mentioned = {user.id for user in message.mentions}
    if bot.user and bot.user.id in mentioned:
        await bot.rest.messages.create(
            message.channel_id,
            components=[ui.text("Hi! Try /roll.")],
            reply_to=message.id,
        )


bot.run()
```

A few things the example takes for granted:

- `bot.run()` reads the token from `BOSTIE_TOKEN`.
- Commands sync on startup, and only when they changed. With `dev_guilds`, they sync to those guilds only.
- A handler that has not answered after 2.5 s is deferred for you.
- Every handler and listener runs in its own task, which starts at once: its code up to the first `await` runs before the next event is handled.
- The listener needs no [privileged intent](https://docs.discord.com/developers/events/gateway#privileged-intents): mentions always arrive, and a message that mentions the bot carries its content too.

[Commands and components](commands.md) covers checks, options, context menus, modals and the rest of what a handler can do, and [the examples](examples.md) are runnable bots for each part of bostie.

## Modules

To split a bot across modules, register commands and listeners on a `bostie.Router()` and add it with `bot.include(router)`. Inside a router, `router.bot` is the bot it was included in. `Router(ephemeral=True, auto_defer=..., default_permissions=..., nsfw=..., contexts=..., integration_types=...)` sets what its commands and routes, and those of the routers it includes, use where they set nothing themselves; the bot's settings come last. `bot.include("mybot.music")` imports that module and adds the Router it names `router`; `bot.exclude(...)` takes out a router or module and all it added, and `bot.reload("mybot.music")` imports the module anew in its place, keeping the old one when that fails. Commands added or removed while the bot runs reach Discord at the next `await bot.sync_commands()`.

## Background tasks

`@bot.every(seconds=300)` runs `async def task()` every 5 minutes while the bot is ready: first when `Started` fires, or when an `ASGIApp` without a gateway starts, then on a fixed beat, skipping the beats a slow run overlaps. `@bot.every(time=datetime.time(9, 30))` runs it daily at 09:30 UTC, or in the time's `tzinfo`, and takes a list of times too. `close()` cancels tasks, and a task that raises goes on at its next time; the error reaches `TaskError` listeners, or the log when there are none.

## Running inside another program

`await bot.start()` and `await bot.close()` run the bot inside an asyncio program that owns the event loop, such as a web app; [Running the bot](running.md) shows how, with FastAPI. `async with bot:` awaits `close()` when the block ends, whether `start()` ran or not.
