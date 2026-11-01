<img src="assets/brand/bostie-logo.svg" alt="bostie logo" width="120">

# bostie

<img src="assets/mascot/bostie-waving.svg" alt="Bostie, a Boston Terrier, waving" width="110" align="right">

A Discord library for Python with a Rust core, designed around reliable voice.

> **Status:** pre-alpha. Text bots work: slash commands, buttons and selects, gateway events, the cache and REST. Voice joins channels, plays audio and receives it.

## Install

bostie is not on PyPI yet. Clone this repository and install the clone into your bot's environment:

```sh
pip install path/to/bostie
```

You need Python 3.12 or newer, Rust 1.95 or newer ([rustup.rs](https://rustup.rs)) and [CMake](https://cmake.org/download/). The install compiles the Rust core and libopus, which takes a few minutes.

## Goals

- Voice comes first. Discord has required end-to-end encrypted voice ([DAVE](https://docs.discord.com/developers/topics/voice-connections)) since March 2026. The voice path runs in Rust, outside the GIL: the 20 ms clock, mixing, Opus, encryption and DAVE. Audio timing does not depend on what your Python code is doing.
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

A few things the example takes for granted:

- `bot.run()` reads the token from `BOSTIE_TOKEN`.
- Commands sync on startup, and only when they changed. With `dev_guilds`, they sync to those guilds only.
- A handler that has not answered after 2.5 s is deferred for you.
- Every handler and listener runs in its own task, which starts at once: its code up to the first `await` runs before the next event is handled.
- Options come from the type hints: `str`, `int`, `float`, `bool`, `User`, `Member`, `PartialChannel`, `Role`, `Attachment` and `User | Role`. `Range[int, 2, 100]` bounds a number or a string's length, a string in `Annotated` is the description, a default or `| None` makes the option optional, and a `Literal`, an `Enum` or `Option(choices=...)` gives choices.
- Messages use [Components V2](https://docs.discord.com/developers/components/overview): `ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file` and `ui.separator` build them, and bostie sets the `IS_COMPONENTS_V2` flag for you.
- Custom id templates such as `reroll:{sides:int}` are parsed into typed handler arguments, and `reroll.id(sides=6)` builds a matching id, so buttons keep working after a restart.
- The listener needs no [privileged intent](https://docs.discord.com/developers/events/gateway#privileged-intents): mentions always arrive, and a message that mentions the bot carries its content too.

Everything bostie does not model yet stays reachable:

- `bot.http.request("GET", "/guilds/{guild_id}/soundboard-sounds", guild_id=...)` calls any endpoint.
- `bot.gateway.send(op=..., d=...)` sends any opcode.
- `@bot.listen_raw("EVENT_NAME")` receives any dispatch as a `RawEvent`.

To split a bot across modules, register commands and listeners on a `bostie.Router()` and add it with `bot.include(router)`. Inside a router, `router.bot` is the bot it was included in.

## Voice

```python
from bostie import Bot, Context, voice

bot = Bot(voice=voice.Defaults(timeout=15.0))
queues: dict[int, voice.Queue] = {}


@bot.command
async def play(ctx: Context, url: str) -> None:
    """Queue a direct media URL."""
    vc = ctx.voice.current or await ctx.voice.connect()
    queue = queues.get(vc.guild_id)
    if queue is None or queue.vc is not vc:
        queue = queues[vc.guild_id] = voice.Queue(vc)
    queue.add(voice.Url(url))
    await ctx.respond(f"{len(queue.upcoming)} queued")


@bot.command
async def skip(ctx: Context) -> None:
    """Skip the current track."""
    if ctx.guild_id in queues:
        queues[ctx.guild_id].skip()
    await ctx.respond("skipped")


@bot.listen
async def on_voice_state(event: voice.StateChange) -> None:
    if event.new is voice.State.DISCONNECTED:
        print(f"voice in {event.guild_id} ended: {event.reason} ({event.detail})")


bot.run()
```

- `ctx.voice.connect()` joins the voice channel of the user who ran the command. `bot.voice.connect(channel_id)` joins any voice or stage channel. In a guild where the bot is already connected, both move it, so `ctx.voice.current or ...` keeps a bot that is playing where it is.
- `connect()` returns once audio can flow, or raises `voice.VoiceConnectError` with the reason. Dropped connections, voice server changes, moves and DAVE transitions are handled for you. `voice.StateChange`, `voice.Moved`, `voice.UserJoin` and the other voice events reach `@bot.listen` and `bot.wait_for`.
- `voice.File`, `voice.Bytes` and `voice.Url` decode Ogg Opus and Vorbis, WebM, FLAC, WAV, MP3 and AAC in M4A in Rust. `voice.FFmpeg(args)` plays anything ffmpeg reads, and `voice.Stream(chunks, format)` plays an async iterator. `vc.play(source)` plays a source on its own, mixed with whatever else plays. `voice.Queue` plays tracks back to back without gaps.
- `vc.sink(format)` is what they all build on: a bounded writer of PCM (16-bit or float, 8 to 192 kHz, mono or stereo) or Opus packets. `await sink.write(data)` waits while `buffer` seconds are queued. A sink that runs dry sends silence; the stream goes on.
- `Bot(voice=voice.Defaults(...))` sets what every connection and sink starts from: self mute and deafen, timeout, encoder, speaking flags, sink buffer, stall policy and stage role. Each call can override them.
- Stage channels are listen-only unless you pass `stage="request"` (ask to speak) or `stage="speak"`.
- Voice needs the `GUILD_VOICE_STATES` intent, which `Intents.DEFAULT` includes.

### Text to speech

Most streaming text-to-speech APIs can return raw PCM. Hand the chunks to `voice.Stream` with their format, and playback starts with the first chunk:

```python
speaking: dict[int, voice.Track] = {}


@bot.command
async def say(ctx: Context, text: str) -> None:
    """Speak; a new /say interrupts the last one."""
    vc = ctx.voice.current or await ctx.voice.connect()
    if (previous := speaking.get(vc.guild_id)) is not None:
        previous.stop()
    chunks = speech(text)
    speaking[vc.guild_id] = await vc.play(voice.Stream(chunks, voice.PCM(24_000, 1)))
    await ctx.respond("speaking")
```

Here `speech(text)` stands for your engine's streaming call: an async iterator of 24 kHz mono 16-bit chunks. The Rust core resamples them to 48 kHz stereo.

### Receiving audio

```python
import asyncio

from bostie import AllowedMentions

captioning: set[asyncio.Task[None]] = set()


async def captions(vc: voice.VoiceConnection, channel_id: int) -> None:
    async with vc.receive(voice.PCM(16_000, 1), markers=True) as rx:
        async for said in voice.utterances(rx):
            text = await speech_to_text(said.data)
            await bot.send_message(
                channel_id,
                f"<@{said.user_id}>: {text}",
                allowed_mentions=AllowedMentions(parse=()),
            )


@bot.command
async def caption(ctx: Context) -> None:
    """Post what people in your voice channel say."""
    vc = ctx.voice.current or await ctx.voice.connect()
    if ctx.channel_id is not None:
        task = asyncio.create_task(captions(vc, ctx.channel_id))
        captioning.add(task)
        task.add_done_callback(captioning.discard)
    await ctx.respond("Captioning. Everyone in this call can see the bot.")
```

Here `speech_to_text(audio)` stands for your speech-to-text engine, which gets 16 kHz mono 16-bit audio.

- `vc.receive(format)` opens a receiver, a bounded async iterator over the call's audio. Open one per consumer, as many as you like. It yields decoded PCM at 8 to 48 kHz, Opus packets, or the raw packets, per user or mixed into one stream, and `users=` or `exclude=` limit whom it hears. Audio nobody asked for is never decrypted or decoded.
- `voice.utterances(rx)` groups frames into one utterance per stretch of speech. `voice.Recorder(vc, "recordings/{guild_id}/{user_id}.ogg")` records each person to an Ogg Opus file.
- `voice.SpeechStart` and `voice.SpeechStop` fire for everyone in the call, and `vc.speaking` says who is talking now.
- A receiver ends when the bot leaves or is moved to another channel, which ends `captions` above.
- Tell people before you record or transcribe them. [docs/voice.md](docs/voice.md#receiving-audio) covers every option, and privacy and consent.

## Bot parameters

Every parameter of `bostie.Bot` is keyword-only and optional.

- `intents`: the gateway intents. The default, `Intents.DEFAULT`, is every intent except the privileged ones. A privileged intent must also be turned on in the Developer Portal.
- `dev_guilds`: guild ids to sync commands to. When set, commands sync to these guilds only and global commands are left alone. Leave it empty to sync globally.
- `auto_sync`: `False` skips the startup sync. `await bot.sync_commands()` syncs again while the bot is running and returns a `bostie.SyncResult` for each scope, saying what differed.
- `auto_defer`: seconds before an unanswered handler is deferred, or `None` to never defer. `@bot.command(auto_defer=...)` overrides it for one command.
- `cache`: `False` leaves `bot.cache` empty. By default it keeps guilds, channels, threads and roles current.
- `allowed_mentions`: who the bot's messages may ping. The default allows users and roles, not `@everyone`.
- `presence`: the `presence` object sent with identify, as a dict in Discord's format.
- `global_rate_limit`: requests per second across all routes, 50 by default. Interaction responses and follow-ups do not count.
- `api_url`: the REST API base URL, for a proxy or a fake Discord in tests.
- `voice`: a `voice.Defaults` with the settings every voice connection and sink starts from.

## License

Licensed under either of [Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT), at your option.

bostie is not affiliated with Discord Inc.
