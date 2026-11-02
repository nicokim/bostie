<img src="assets/brand/bostie-logo.svg" alt="bostie logo" width="120">

# bostie

<img src="assets/mascot/bostie-waving.svg" alt="Bostie, a Boston Terrier, waving" width="110" align="right">

A Discord library for Python with a Rust core, designed around reliable voice.

> **Status:** pre-alpha. Text bots work: slash, user and message commands, buttons, selects and modals, gateway events, the cache and REST. Voice joins channels, plays audio and receives it.

## Install

bostie is not on PyPI yet. Clone this repository and install the clone into your bot's environment:

```sh
pip install path/to/bostie
```

You need Python 3.12 or newer, Rust 1.95 or newer ([rustup.rs](https://rustup.rs)) and [CMake](https://cmake.org/download/). The install compiles the Rust core and libopus, which takes a few minutes.

## Goals

- Voice comes first. Discord has required end-to-end encrypted voice ([DAVE](https://docs.discord.com/developers/topics/voice-connections)) since March 2026. The voice path runs in Rust, outside the GIL: the 20 ms clock, mixing, Opus, encryption and DAVE. Audio timing does not depend on what your Python code is doing.
- Bots keep working when Discord adds things. Unknown fields, enum values, components and interaction types never break decoding. Every event keeps its raw payload, and raw escape hatches reach events and endpoints bostie does not model yet.
- Models are typed as Discord documents them: a field Discord always sends is never `None`, and a partial object such as an option's member is a `PartialMember`, not a `Member` with holes. A message a webhook sent, interaction responses included, is a `WebhookMessage` whose `author` is a `PartialUser`; any other is a `Message` with a full `User`.
- The busy paths run in Rust: the gateway, compression, HTTP and rate limiting. Python decodes raw JSON into compact `msgspec` models.

## Example

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
- Options come from the type hints: `str`, `int`, `float`, `bool`, `User`, `Member`, `PartialChannel`, `Role`, `Attachment` and `User | Role`. `Range[int, 2, 100]` bounds a number or a string's length, a string in `Annotated` is the description, a default or `| None` makes the option optional, and a `Literal`, an `Enum` or `Option(choices=...)` gives choices.
- `@bot.user_command` and `@bot.message_command` add a command to the context menu of a user or a message. The handler takes the target after the context, typed `User` or `Member`, or `Message`, `WebhookMessage` or both.
- `ctx.modal(ui.modal(...))` opens a form from a command or a component. `@bot.on_modal` routes its submit by custom id template, like a component, and `ctx.text`, `ctx.values`, `ctx.ids`, `ctx.attachments`, `ctx.choice` and `ctx.checked` read the answers by their custom ids: `ctx.values` gives the options picked as strings, `ctx.ids` the users, roles, channels or uploads picked as ids.
- Messages use [Components V2](https://docs.discord.com/developers/components/overview): `ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file` and `ui.separator` build them, and bostie sets the `IS_COMPONENTS_V2` flag for you. Such a message cannot also carry content, embeds, stickers or a poll, and bostie raises `ValueError` before sending one.
- Custom id templates such as `reroll:{sides:int}` are parsed into typed handler arguments, and `reroll.id(sides=6)` builds a matching id, so buttons keep working after a restart.
- The listener needs no [privileged intent](https://docs.discord.com/developers/events/gateway#privileged-intents): mentions always arrive, and a message that mentions the bot carries its content too.

`bot.rest` has every documented REST operation, grouped by resource, once the bot has started:

- `await bot.rest.channels.edit(channel_id, name="news", topic=None)`: ids come first and positional, in the order of the URL. A keyword you leave out is left as it is, and `None` clears the field.
- `reason="..."` goes to the audit log on the operations that take one, such as `bot.rest.bans.create(guild_id, user_id, reason="spam")`.
- A list Discord pages is an async iterator that pages for you: `async for message in bot.rest.messages.list(channel_id, limit=500)`. `bot.rest.audit_log.list(guild_id)` yields whole pages, with the users and webhooks the entries refer to.
- Image options such as `icon`, `avatar` and `banner` take a `File`.
- When an edit lists `attachments`, only those stay. A listed `Attachment` sends its `description` and spoiler flag too, so `msgspec.structs.replace(attachment, description="...")` changes its alt text.
- Results are bostie models, and they do not change `bot.cache`: the gateway events that follow do.

Everything bostie does not model yet stays reachable:

- `bot.http.request("GET", "/guilds/{guild_id}/some-new-route", guild_id=...)` calls any endpoint, such as one Discord added after this release, and returns its JSON.
- `bot.gateway.send(op=..., d=...)` sends any opcode: op 3 to every shard, a payload with a `guild_id` to that guild's shard, one with `guild_ids` to each of their shards with its share of the ids, anything else to shard 0. `bot.gateway.shard(id).send(...)` picks the shard yourself.
- `@bot.listen_raw("EVENT_NAME")` receives any dispatch as a `RawEvent`.
- `ui.raw({...})` puts a component that `ui` has no builder for into a row, section, container, label or modal.
- An `InteractionCreate` listener gets every interaction, routed or not. A type bostie does not model arrives as an `UnknownInteraction`, with the payload in `raw`.

To split a bot across modules, register commands and listeners on a `bostie.Router()` and add it with `bot.include(router)`. Inside a router, `router.bot` is the bot it was included in.

## Events

`@bot.listen` takes the typed events of `bostie.events`. `await bot.wait_for(Event, check=...)` waits for the next one, and `async with bot.stream(Event, check=...) as events:` queues every match for `async for` until the block ends; bound either with `asyncio.timeout()`.

- `Ready` is Discord's READY: one shard started a session, and the guilds it lists arrive afterwards as `GuildCreate` events. It fires again for every new session.
- `ShardReady` follows once those guilds have arrived, or after `guild_ready_timeout` seconds without one, so the shard's guilds are in `bot.cache`. It too fires for every new session.
- `Started` fires once per `start()`, when every shard is ready. `await bot.wait_until_ready()` returns at that moment and `bot.is_ready` turns true. `Stopping` fires when `close()` begins after `Started`, and `close()` gives its listeners up to 5 s while the gateway and REST still work.
- `GuildCreate.joined` is true for a guild the bot was just added to, false for one that became available.
- Update events carry the object as the cache held it in `old`, and delete events the removed one: `GuildDelete.guild`, `RoleDelete.role`, `ThreadDelete.thread`, `MessageDelete.message`.
- `ShardDisconnect` says a shard lost its connection, with the close code and whether it resumes the session.
- An exception in a listener goes to the `ListenerError` listeners, or to the log when there are none. `router.remove_listener(callback)` stops a listener.
- Registering a listener for an event that none of the bot's intents delivers logs a warning.

`await bot.request_members(guild_id, query=..., limit=...)` or `user_ids=...` asks the gateway for members and gathers every chunk of the answer, with `presences=True` their presences too. `limit` goes up to 100 and `user_ids` takes at most 100. Without `query` or `user_ids` it asks for every member, which needs `Intents.GUILD_MEMBERS`; presences need `Intents.GUILD_PRESENCES`. It raises `bostie.MemberRequestRateLimited` with `retry_after` when Discord refuses, and `TimeoutError` when a chunk takes over `timeout` seconds, 30 by default. `await bot.request_soundboard_sounds(guild_id)` and `await bot.request_channel_info(guild_id)` ask the gateway for a guild's soundboard sounds and its channels' voice status. A request raises `StateError` when its shard starts a new session before the answer.

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
- `voice.File`, `voice.Bytes` and `voice.Url` decode Ogg Opus and Vorbis, WebM, FLAC, WAV, MP3 and AAC in M4A in Rust. `voice.FFmpeg(args)` plays anything ffmpeg reads, and `voice.Stream(chunks, format)` plays an async iterator. `vc.play(source)` plays a source on its own, mixed with whatever else plays, and returns its `voice.Track`: `await track.started()` waits until its audio starts, for a "now playing" message, and `await track.wait()` until it ends. `voice.Queue` plays tracks back to back without gaps. `queue.add(source, index=0)` plays a source next, and `queue.move()`, `queue.shuffle()`, `queue.remove()` and `queue.skip()` change what comes after the current track. `voice.Queue(vc, crossfade=3.0)` fades each track into the next over 3 s, which keeps 3 s more audio buffered. `queue.loop = "track"` plays the current track again and again, and `"queue"` plays every track again at the end; a skip with `"track"` moves on to the next track. A source plays again through `source.again()`, so a `voice.Stream`, which cannot, plays once.
- When the connection of a `voice.Url` drops, it reconnects up to three times in a row: from where it broke when the server takes range requests, or from the live edge for a stream with no length. Otherwise the track ends with `EndReason.ERROR`.
- `vc.sink(format)` is what they all build on: a bounded writer of PCM (16-bit or float, 8 to 192 kHz, mono or stereo) or Opus packets. `await sink.write(data)` waits while `buffer` seconds are queued. A sink that runs dry sends silence; the stream goes on. `vc.sink(format, after=other)` stays silent until `other` closes or plays out what was written before its last `drain()`, then starts on the next 20 ms tick: that is how a queue stays gapless when an Opus track follows a PCM one.
- `vc.sinks` lists what plays on a connection, and `vc.stop()` closes every sink so nothing does. Their tracks end `STOPPED`, and a queue takes new tracks afterwards.
- `Bot(voice=voice.Defaults(...))` sets what every connection and sink starts from: self mute and deafen, timeout, encoder, speaking flags, sink buffer, stall policy, stage role and a queue's crossfade. Each call can override them.
- Stage channels are listen-only unless you pass `stage="request"` (ask to speak) or `stage="speak"`. A connection asks for its role each time it enters a stage channel.
- Voice needs the `GUILD_VOICE_STATES` intent, which `Intents.DEFAULT` includes.
- What the Rust core logs, such as a DAVE session reset or a dropped frame, goes to Python `logging` under `bostie.voice`, `bostie.gateway` and `bostie.rest`. The core reads those loggers' levels when the bot starts, and levels that are off cost nothing.

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
            await bot.rest.messages.create(
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
- `cache`: `False` leaves `bot.cache` empty. By default it keeps guilds current, `Guild.roles`, `emojis` and `stickers` included, along with channels, active threads, voice states and the bot's own member in each guild, but no other members. `bot.cache.permissions(channel_id, member)` and `bot.cache.guild_permissions(guild_id, member)` compute a member's permissions from it as Discord does, the bot's own when `member` is left out. `Guild.member_count` follows joins and leaves only with `Intents.GUILD_MEMBERS`.
- `max_messages`: how many of the latest messages the cache keeps, 0 by default. `bot.cache.message(id)` reads them, and `MessageUpdate.old`, `MessageDelete.message` and `MessageDeleteBulk.messages` carry them.
- `guild_ready_timeout`: seconds a shard waits for the next of its guilds before it counts as ready anyway, 2 by default. Guilds in an outage arrive later.
- `allowed_mentions`: who the bot's messages may ping. The default allows users and roles, not `@everyone`.
- `status`, `activities`, `afk` and `idle_since`: the presence the bot shows, online with no activity by default. `bostie.activity` builds activities, such as `activity.playing("chess")` or `activity.custom("Rolling dice")`. `await bot.update_presence(...)` changes any of them on every shard, and sessions that start later show the change too.
- `global_rate_limit`: requests per second across all routes, 50 by default. Interaction responses and follow-ups do not count.
- `api_url`: the REST API base URL, for a proxy or a fake Discord in tests.
- `voice`: a `voice.Defaults` with the settings every voice connection and sink starts from.
- `shard_count`: how many shards the bot splits its guilds across. The default, `"auto"`, takes Discord's recommendation at each start, so one process runs every shard it needs.
- `shard_ids`: the shards this process runs, for a bot split across processes. It needs an explicit `shard_count`; the default runs them all. `Started` waits for every shard here, and each event carries its `shard_id`.
- `before_identify`: an async function that gets a shard id before each identify of that shard and returns once it may identify. Processes of one bot can share Discord's `max_concurrency` through it; an exception is logged and the shard identifies anyway.

## Errors

A bad argument raises `ValueError` or `TypeError`. Every other error bostie raises, the Rust core's included, is a `bostie.BostieError`:

- `HTTPException` and its subclasses such as `NotFound` when Discord answers with an error status, `TransportError` when a request fails before it answers, and `GatewayClosed` when Discord ends the gateway session for good.
- `StateError`, also a `RuntimeError`, when an object is not in a state that allows the call, such as `bot.http` or `bot.rest` before `start()`. `ShardNotRunning` and `voice.AlreadyConnected` are kinds of it.
- `MissingIntent`, also a `ValueError`, when a feature needs an intent the bot did not ask for, and `StartupError`, also an `OSError`, when bostie cannot set up a thread, socket, HTTP client or TLS context it needs.
- `CommandError` for commands and interactions, and `voice.VoiceError` for voice.

## License

Licensed under either of [Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT), at your option.

bostie is not affiliated with Discord Inc.
