<img src="assets/brand/bostie-logo.svg" alt="bostie logo" width="120">

# bostie

<img src="assets/mascot/bostie-waving.svg" alt="Bostie, a Boston Terrier, waving" width="110" align="right">
<!-- --8<-- [start:intro] -->

A Discord library for Python with a Rust core, designed around reliable voice.

> **Status:** pre-alpha. Text bots work: slash, user and message commands, buttons, selects and modals, gateway events, the cache and REST. Voice joins channels, plays audio and receives it.

## Install

bostie is not on PyPI yet. Clone this repository and install the clone into your bot's environment:

```sh
pip install path/to/bostie
```

You need Python 3.12 or newer, Rust 1.95 or newer ([rustup.rs](https://rustup.rs)) and [CMake](https://cmake.org/download/). The install compiles the Rust core and libopus, which takes a few minutes.

`bostie.__version__` is the installed version, and `python -m bostie` prints it with the Python and platform versions for a bug report.

## Goals

- Voice comes first. Discord has required end-to-end encrypted voice ([DAVE](https://docs.discord.com/developers/topics/voice-connections)) since March 2026. The voice path runs in Rust, outside the GIL: the 20 ms clock, mixing, Opus, encryption and DAVE. Audio timing does not depend on what your Python code is doing.
- Bots keep working when Discord adds things. Unknown fields, enum values, components and interaction types never break decoding. Every event keeps its raw payload, and raw escape hatches reach events and endpoints bostie does not model yet.
- Models are typed as Discord documents them: a field Discord always sends is never `None`, and a partial object such as an option's member is a `ResolvedMember`, which has no `deaf` or `mute`, not a `Member` with holes. A message a webhook sent, interaction responses included, is a `WebhookMessage` whose `author` is a `PartialUser`; any other is a `Message` with a full `User`.
- The busy paths run in Rust: the gateway, compression, HTTP and rate limiting. Python decodes raw JSON into compact `msgspec` models.

<!-- --8<-- [end:intro] -->

## Example

```python
import random

import bostie
from bostie import ComponentContext, Context, Range, ui, voice

bot = bostie.Bot()


@bot.command
async def roll(ctx: Context, sides: Range[int, 2, 100] = 6) -> None:
    """Roll a die."""
    again = ui.button("Again", custom_id=reroll.id(sides=sides))
    rolled = ui.text(f"d{sides}: {random.randint(1, sides)}")
    await ctx.respond(components=[rolled, ui.row(again)])


@bot.on_component("reroll:{sides:int}")
async def reroll(ctx: ComponentContext, sides: int) -> None:
    await ctx.update(components=[ui.text(f"d{sides}: {random.randint(1, sides)}")])


@bot.command
async def play(ctx: Context, url: str) -> None:
    """Play a direct media URL in your voice channel."""
    vc = ctx.voice.current or await ctx.voice.connect()
    await vc.play(voice.Url(url))
    await ctx.respond("Playing")


bot.run()
```

`bot.run()` reads the token from `BOSTIE_TOKEN`, and commands sync on startup when they changed.

## Documentation

The [guide](docs/index.md) starts with a first bot. Then, by topic:

- [Commands and components](docs/commands.md): options, checks, context menus, modals, selects, Components V2 and waiting for a click.
- [Events](docs/events.md): typed gateway events, waiting for them, and asking the gateway for members.
- [REST](docs/rest.md): every documented operation on `bot.rest`, REST without a gateway, webhooks, OAuth2, and raw requests for what bostie does not model yet.
- [Interactions over HTTP](docs/http.md): an ASGI app for the Interactions Endpoint URL and the Webhook Events URL.
- [Voice](docs/voice.md): playing, queues, filters, text to speech, receiving and recording.
- [Helpers](docs/helpers.md): CDN URLs, message formatting, snowflakes, locales, permissions and colors.
- [Testing](docs/testing.md): a fake Discord to test a bot against.
- [Bot parameters](docs/bot.md), [Errors](docs/errors.md), and [Running the bot](docs/running.md) inside a web app.

[examples/](examples) holds runnable bots for commands, components, modals, an Activity, events, background tasks, modules, a web app, interactions over HTTP, music and recording, and scripts for REST without a gateway, webhooks and linked roles.

## License

Licensed under either of [Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT), at your option.

bostie is not affiliated with Discord Inc.
