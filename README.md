<img src="assets/brand/bostie-logo.svg" alt="bostie logo" width="120">

# bostie

<img src="assets/mascot/bostie-waving.svg" alt="Bostie, a Boston Terrier, waving" width="110" align="right">

A Discord library for Python with a Rust core, designed around reliable voice.

> **Status:** pre-alpha. Text bots work: slash commands, buttons and selects, gateway events, the cache and REST. Voice comes next.

## Install

bostie is not on PyPI yet. Clone this repository and install the clone into your bot's environment:

```sh
pip install path/to/bostie
```

You need Python 3.12 or newer and Rust 1.95 or newer ([rustup.rs](https://rustup.rs)). The install compiles the Rust core, which can take a few minutes.

## Goals

- Voice is the next feature. Discord has required end-to-end encrypted voice ([DAVE](https://docs.discord.com/developers/topics/voice-connections)) since March 2026, and most Python libraries still struggle with it, especially when receiving audio. The whole voice path is planned in Rust, outside the GIL, so audio timing does not depend on what your Python code is doing.
- Bots keep working when Discord adds things. Unknown fields, enum values and components never break decoding. Every event keeps its raw payload, and raw escape hatches reach events and endpoints bostie does not model yet.
- The busy paths run in Rust: the gateway, compression, HTTP and rate limiting. Python decodes raw JSON into compact `msgspec` models.

## Example

```python
from enum import Enum
from typing import Annotated, Literal

import bostie
from bostie import ComponentContext, Context, Range, ui
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


@bot.listen
async def wave_back(event: MessageCreate) -> None:
    message = event.message
    mentioned = {user.id for user in message.mentions}
    if bot.user and bot.user.id in mentioned and message.channel_id:
        await bot.send_message(
            message.channel_id,
            components=[ui.text("Hi! Try /roll.")],
            reply_to=message.id,
        )


bot.run()
```

What the example relies on:

- `bot.run()` reads the token from `BOSTIE_TOKEN`.
- Commands sync on startup, and only when they changed. With `dev_guilds`, they sync to those guilds only.
- A handler that has not answered after 2.5 s is deferred for you.
- Options come from the type hints. `str`, `int`, `float` and `bool` map to the matching option types, `User` or `Member` to a user, `Channel` to a channel, `Role` to a role, `Attachment` to an attachment, and `User | Role` to a mentionable.
- `Range[int, 2, 100]` bounds a number, or a string's length. A string in `Annotated` is the description. A default or `| None` makes the option optional.
- Choices come from a `Literal` of strings or ints, an `Enum`, or `Option(choices=...)`.
- Messages use [Components V2](https://docs.discord.com/developers/components/overview): `ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file` and `ui.separator` build them, and bostie sets the `IS_COMPONENTS_V2` flag for you.
- Custom id templates such as `reroll:{sides:int}` are parsed into typed handler arguments, and `reroll.id(sides=6)` builds a matching id, so buttons keep working after a restart.
- The listener needs no [privileged intent](https://docs.discord.com/developers/events/gateway#privileged-intents): mentions always arrive, and a message that mentions the bot carries its content too.

Everything bostie does not model yet stays reachable:

- `bot.http.request("GET", "/guilds/{guild_id}/soundboard-sounds", guild_id=...)` calls any endpoint.
- `bot.gateway.send(op=..., d=...)` sends any opcode.
- `@bot.listen_raw("EVENT_NAME")` receives any dispatch as a `RawEvent`.

To split a bot across modules, register commands and listeners on a `bostie.Router()` and add it with `bot.include(router)`. Inside a router, `router.bot` is the bot it was included in.

## Bot parameters

Every parameter of `bostie.Bot` is keyword-only and optional.

- `intents`: the gateway intents. The default, `Intents.DEFAULT`, is every intent except the privileged ones. A privileged intent must also be turned on in the Developer Portal.
- `dev_guilds`: guild ids to sync commands to. When set, commands sync to these guilds only and global commands are left alone. Leave it empty to sync globally.
- `sync_commands`: `False` skips the startup sync. `await bot.sync_commands()` syncs again while the bot is running.
- `auto_defer`: seconds before an unanswered handler is deferred, or `None` to never defer. `@bot.command(auto_defer=...)` overrides it for one command.
- `cache`: `False` leaves `bot.cache` empty. By default it keeps guilds, channels, threads and roles current.
- `allowed_mentions`: who the bot's messages may ping. The default allows users and roles, not `@everyone`.
- `presence`: the `presence` object sent with identify, as a dict in Discord's format.
- `global_rate_limit`: requests per second across all routes, 50 by default. Interaction responses and follow-ups do not count.
- `api_url`: the REST API base URL, for a proxy or a fake Discord in tests.

## Layout

| Path | What lives there |
|---|---|
| `crates/bostie-core` | Rust core: async runtime, networking, protocol. No Python dependency. |
| `crates/bostie-native` | Thin PyO3 bindings exposing the core as `bostie._native`. |
| `python/bostie` | The public Python API. |
| `tests` | Python tests, an offline fake Discord (`tests/fakediscord`), sample payloads (`tests/fixtures`) and a check against real Discord (`tests/live`). |
| `assets` | The logo and the mascot. |

## License

Licensed under either of [Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT), at your option.

bostie is not affiliated with Discord Inc.
