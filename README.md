<img src="assets/brand/bostie-logo.svg" alt="bostie logo" width="120">

# bostie

<img src="assets/mascot/bostie-waving.svg" alt="Bostie, a Boston Terrier, waving" width="110" align="right">
<!-- --8<-- [start:guide] -->

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
- Options come from the type hints: `str`, `int`, `float`, `bool`, `User`, `ResolvedMember`, `PartialChannel`, `Role`, `Attachment` and `User | Role`. `Range[int, 2, 100]` bounds a number or a string's length, a string in `Annotated` is the description, a default or `| None` makes the option optional, and a `Literal`, an `Enum` or `Option(choices=...)` gives choices.
- `@bot.user_command` and `@bot.message_command` add a command to the context menu of a user or a message. The handler takes the target after the context, typed `User` or `ResolvedMember`, or `Message`, `WebhookMessage` or both.
- `ctx.modal(ui.modal(...))` opens a form from a command or a component. `@bot.on_modal` routes its submit by custom id template, like a component, and `ctx.text`, `ctx.values`, `ctx.ids`, `ctx.users`, `ctx.members`, `ctx.roles`, `ctx.channels`, `ctx.attachments`, `ctx.choice` and `ctx.checked` read the answers by their custom ids: `ctx.values` gives the options picked as strings, `ctx.ids` the users, roles, channels or uploads picked as ids, and the others what those ids resolve to.
- A select on a message gives the same through `ctx.values`, `ctx.ids`, `ctx.users`, `ctx.members`, `ctx.roles` and `ctx.channels` of its `ComponentContext`.
- Messages use [Components V2](https://docs.discord.com/developers/components/overview): `ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file` and `ui.separator` build them, and bostie sets the `IS_COMPONENTS_V2` flag for you. Such a message cannot also carry content, embeds, stickers or a poll, and bostie raises `ValueError` before sending one.
- `ui.premium_button(sku_id)` offers one of the app's SKUs for sale, and every component builder takes an `id=`. The builders raise `ValueError` for what the docs' limits rule out, such as a sixth button in a row or a modal title over 45 characters.
- Custom id templates such as `reroll:{sides:int}` are parsed into typed handler arguments, and `reroll.id(sides=6)` builds a matching id, so buttons keep working after a restart.
- The listener needs no [privileged intent](https://docs.discord.com/developers/events/gateway#privileged-intents): mentions always arrive, and a message that mentions the bot carries its content too.

[examples/][examples] holds runnable bots for commands, components, modals, events, background tasks, modules, a web app, interactions over HTTP, music and recording, and scripts for REST without a gateway, webhooks and linked roles.

`bot.rest` has every documented REST operation the bot token reaches, grouped by resource, once the bot has started. Those that need a user's OAuth2 token are on a `BearerClient`, below; interaction answers go through the contexts, and deprecated or legacy routes are left out.

- `await bot.rest.channels.edit(channel_id, name="news", topic=None)`: ids come first and positional, in the order of the URL. A keyword you leave out is left as it is, and `None` clears the field.
- `reason="..."` goes to the audit log on the operations that take one, such as `bot.rest.bans.create(guild_id, user_id, reason="spam")`. It takes 1 to 512 characters.
- A list Discord pages is an async iterator that pages for you: `async for message in bot.rest.messages.list(channel_id, limit=500)`. `bot.rest.audit_log.list(guild_id)` yields whole pages, with the users and webhooks the entries refer to.
- Image options such as `icon`, `avatar` and `banner` take a `File`.
- When an edit lists `attachments`, only those stay. A listed `Attachment` sends its `description` and spoiler flag too, so `msgspec.structs.replace(attachment, description="...")` changes its alt text.
- Results are bostie models, and they do not change `bot.cache`: the gateway events that follow do.
- `async with bostie.RESTClient(token) as client:` opens the same operations on `client.rest` without a gateway, for scripts and web backends. The token defaults to `BOSTIE_TOKEN`.
- `async with bostie.WebhookClient(url) as webhook:` posts through one webhook from its URL alone, with no bot token: `await webhook.execute("deploy done")`, and `edit_message`, `fetch_message` and `delete_message` for what it sent. `execute_slack` and `execute_github` take a payload in Slack's or GitHub's format. `bostie.SyncWebhookClient(url)` has the same methods for code without an event loop, each call running one.
- `bostie.oauth2.invite_url(client_id, permissions=..., scopes=["bot"])` builds an install link, and `oauth2.authorize_url(client_id, scopes=[...], redirect_uri=...)` the link that starts an OAuth2 code grant. `async with bostie.BearerClient() as oauth:` turns the code into a token with `oauth.rest.oauth2.exchange_code(...)`, and refreshes, revokes or gets one from client credentials. `BearerClient(token.access_token)` then reaches what the user granted on its `rest`: `users.fetch_current()`, `guilds.list()`, `users.list_connections()`, `members.fetch_current(guild_id)`, `commands.edit_permissions(...)`, and `users.update_role_connection(application_id, ...)` for linked roles.
- A Social SDK game backend manages lobbies with the bot token on `bot.rest.lobbies`, while a `BearerClient` with a player's token joins them and sends lobby messages. `bot.rest.identities` writes the stats Game Stats Widgets show, and `bot.rest.partner_sdk` gets provisional account tokens and moderates direct messages.

Everything bostie does not model yet stays reachable:

- `bot.http.request("GET", "/guilds/{guild_id}/some-new-route", guild_id=...)` calls any endpoint, such as one Discord added after this release, and returns its JSON.
- `bot.gateway.send(op=..., d=...)` sends any opcode: op 3 to every shard, a payload with a `guild_id` to that guild's shard, one with `guild_ids` to each of their shards with its share of the ids, anything else to shard 0. `bot.gateway.shard(id).send(...)` picks the shard yourself.
- `@bot.listen_raw("EVENT_NAME")` receives any dispatch as a `RawEvent`.
- `ui.raw({...})` puts a component that `ui` has no builder for into a row, section, container, label or modal.
- An `InteractionCreate` listener gets every interaction, routed or not. A type bostie does not model arrives as an `UnknownInteraction`, with the payload in `raw`.

To split a bot across modules, register commands and listeners on a `bostie.Router()` and add it with `bot.include(router)`. Inside a router, `router.bot` is the bot it was included in. `bot.include("mybot.music")` imports that module and adds the Router it names `router`; `bot.exclude(...)` takes out a router or module and all it added, and `bot.reload("mybot.music")` imports the module anew in its place, keeping the old one when that fails. Commands added or removed while the bot runs reach Discord at the next `await bot.sync_commands()`.

`@bot.every(seconds=300)` runs `async def task()` every 5 minutes while the bot is ready: first when `Started` fires, or when an `ASGIApp` without a gateway starts, then on a fixed beat, skipping the beats a slow run overlaps. `@bot.every(time=datetime.time(9, 30))` runs it daily at 09:30 UTC, or in the time's `tzinfo`, and takes a list of times too. `close()` cancels tasks, and a task that raises goes on at its next time; the error reaches `TaskError` listeners, or the log when there are none.

`await bot.start()` and `await bot.close()` run the bot inside an asyncio program that owns the event loop, such as a web app; [docs/running.md][running] shows how, with FastAPI.

## Events

`@bot.listen` takes the typed events of `bostie.events`. `await bot.wait_for(Event, check=...)` waits for the next one, and `async with bot.stream(Event, check=...) as events:` queues every match for `async for` until the block ends; bound either with `asyncio.timeout()`.

- `Starting` fires once per `start()`, when REST works and before commands sync and the gateway connects: open a database pool or include more routers there. `start()` awaits its listeners one at a time and raises what one raises. `Stopped` fires at the end of `close()` for every `start()` that sent `Starting`, once the gateway and REST are gone: close the pool there.
- `Ready` is Discord's READY: one shard started a session, and the guilds it lists arrive afterwards as `GuildCreate` events. It fires again for every new session.
- `ShardReady` follows once those guilds have arrived, with their members when the cache keeps members, or after `guild_ready_timeout` seconds without one, so the shard's guilds are in `bot.cache`. It too fires for every new session.
- `Started` fires once per `start()`, when every shard is ready. `await bot.wait_until_ready()` returns at that moment and `bot.is_ready` turns true. `Stopping` fires when `close()` begins after `Started`, and `close()` gives its listeners up to 5 s while the gateway and REST still work.
- `GuildCreate.joined` is true for a guild the bot was just added to, false for one that became available.
- Update events carry the object as the cache held it in `old`, and delete events the removed one: `GuildDelete.guild`, `RoleDelete.role`, `ThreadDelete.thread`, `MemberRemove.member`, `MessageDelete.message`.
- `ShardDisconnect` says a shard lost its connection, with the close code and whether it resumes the session.
- `ShardStop` says a shard stopped for good, because `close()` ran or Discord ended it with a close code, and `ShardBackpressure` that a shard paused reading because listeners fall behind.
- An exception in a listener goes to the `ListenerError` listeners, or to the log when there are none. `router.remove_listener(callback)` stops a listener.
- Events of your own subclass `bostie.events.BotEvent`, as in `class LevelUp(BotEvent): user_id: int`, and `bot.dispatch(LevelUp(user_id=...))` hands one to its listeners, `wait_for` and `stream`.
- Registering a listener for an event that none of the bot's intents delivers logs a warning.
- Some events only reach a Webhook Events URL, such as `ApplicationAuthorized` when a user installs the app. `bostie.verify_signature(public_key, signature, timestamp, body)` checks that Discord sent a request to such a URL or to an Interactions Endpoint URL, and `bostie.decode_webhook_event(body)` decodes its event, or returns None for Discord's PING.

`await bot.request_members(guild_id, query=..., limit=...)` or `user_ids=...` asks the gateway for members and gathers every chunk of the answer, with `presences=True` their presences too. `limit` goes up to 100 and `user_ids` takes at most 100. Without `query` or `user_ids` it asks for every member, which needs `Intents.GUILD_MEMBERS`; presences need `Intents.GUILD_PRESENCES`. It raises `bostie.MemberRequestRateLimited` with `retry_after` when Discord refuses, and `TimeoutError` when a chunk takes over `timeout` seconds, 30 by default. `await bot.request_soundboard_sounds(guild_id)` and `await bot.request_channel_info(guild_id)` ask the gateway for a guild's soundboard sounds and its channels' voice status. A request raises `StateError` when its shard starts a new session before the answer.

## Interactions over HTTP

`app = bostie.ASGIApp(bot)` is an ASGI app for the Interactions Endpoint URL and the Webhook Events URL of the Developer Portal. Run it with any ASGI server, such as `uvicorn mybot:app`, instead of `bot.run()`:

- It checks each request's signature with the app's public key, answers Discord's PINGs, and runs commands, components and modals with the same routers and contexts as over the gateway.
- Webhook events, such as `ApplicationAuthorized`, reach `@bot.listen`, `bot.wait_for` and `bot.stream`. A bot on the gateway can serve them through the same app.
- The server's lifespan starts the bot's REST, without a gateway, sends `Starting`, syncs commands and starts the bot's tasks; at shutdown it closes the bot, which sends `Stopped`. Mounted in another framework, call `await app.start()` and `await app.close()` from that framework's lifespan.
- Without a gateway there is no voice, `bot.cache` stays empty, gateway events never arrive, `Started` never fires and `await bot.wait_until_ready()` never returns.

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

- `ctx.voice.connect()` joins the voice channel of the user who ran the command, which it reads from `bot.cache`, or asks Discord for when the guild is not cached. `bot.voice.connect(channel_id)` joins any voice or stage channel. In a guild where the bot is already connected, both move it, so `ctx.voice.current or ...` keeps a bot that is playing where it is.
- `connect()` returns once audio can flow, or raises `voice.VoiceConnectError` with the reason. Dropped connections, voice server changes, moves and DAVE transitions are handled for you. `voice.StateChange`, `voice.Moved`, `voice.UserJoin` and the other voice events reach `@bot.listen` and `bot.wait_for`.
- `await bot.voice.connect_external(guild_id, channel_id)` joins for an external voice engine and returns the voice state and server it connects with; bostie then plays nothing in that guild. `async with external.updates() as updates:` hands on the later voice servers and the bot's voice states there.
- `voice.File`, `voice.Bytes` and `voice.Url` decode Ogg Opus and Vorbis, WebM, FLAC, WAV, MP3 and AAC in M4A in Rust, and `voice.Url` also plays HLS (`.m3u8`) playlists, live or not. Once a source is open, `source.tags` holds the title, artist and album its tags name. `voice.FFmpeg(args)` plays anything ffmpeg reads, and `voice.Stream(chunks, format)` plays an async iterator.
- When the connection of a `voice.Url` drops, it reconnects up to three times in a row: from where it broke when the server takes range requests, or from the live edge for a stream with no length. Otherwise the track ends with `EndReason.ERROR`.
- `vc.play(source)` plays a source on its own, mixed with whatever else plays, and returns its `voice.Track`: `await track.started()` waits until its audio starts, for a "now playing" message, and `await track.wait()` until it ends.
- `voice.Queue` plays tracks back to back without gaps. `queue.add(source, index=0)` plays a source next, and `queue.move()`, `queue.shuffle()`, `queue.remove()` and `queue.skip()` change what comes after the current track. `voice.Queue(vc, crossfade=3.0)` fades each track into the next over 3 s, which keeps 3 s more audio buffered. `queue.loop = "track"` plays the current track again and again, and `"queue"` plays every track again at the end; a skip with `"track"` moves on to the next track. A source plays again through `source.again()`, so a `voice.Stream`, which cannot, plays once. `voice.Queue(vc, history=20)` keeps the last 20 tracks that played in `queue.history`, and `queue.back()` plays the last of them again.
- `voice.Filters(equalizer=..., bass=..., speed=..., pitch=...)` applies an equalizer, a bass boost and speed and pitch changes in Rust, on a sink, a track or a queue, and can change while it plays.
- `vc.sink(format)` is what they all build on: a bounded writer of PCM (16-bit or float, 8 to 192 kHz, mono or stereo) or Opus packets. `await sink.write(data)` waits while `buffer` seconds are queued. A sink that runs dry sends silence; the stream goes on. `vc.sink(format, after=other)` stays silent until `other` closes or plays out what was written before its last `drain()`, then starts on the next 20 ms tick: that is how a queue stays gapless when an Opus track follows a PCM one.
- `vc.sinks` lists what plays on a connection, and `vc.stop()` closes every sink so nothing does. Their tracks end `STOPPED`, and a queue takes new tracks afterwards.
- `Bot(voice=voice.Defaults(...))` sets what every connection and sink starts from: self mute and deafen, timeout, encoder, speaking flags, sink buffer, stall policy, stage role, and a queue's crossfade and history. Each call can override them. `voice.EncoderSettings` holds the Opus bitrate, application, error correction, complexity, bandwidth and signal type.
- Stage channels are listen-only unless you pass `stage="request"` (ask to speak) or `stage="speak"`. A connection asks for its role each time it enters a stage channel.
- `await voice.message("hello.ogg")` reads an audio file, or takes its bytes, into a `bostie.File` for a voice message: Rust decodes it to measure its duration and waveform. A message with that file is sent with `IS_VOICE_MESSAGE` set, and may hold nothing else. Discord clients send 48 kHz mono Ogg Opus.
- `voice.RtcpPacket` brings the RTCP reports Discord's voice server sends, such as how much of the bot's audio it lost.
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
- `voice.utterances(rx)` groups frames into one utterance per stretch of speech, and with `level=-45.0` cuts by loudness instead, which `frame.level` reports in dBFS. `voice.Recorder(vc, "recordings/{guild_id}/{user_id}.ogg")` records each person to an Ogg Opus file, or a WAV file for a `.wav` path, and `mix=True` records the whole call to one file. `max_duration` and `max_size` end a recording on its own.
- `voice.SpeechStart` and `voice.SpeechStop` fire for everyone in the call, and `vc.speaking` says who is talking now.
- A receiver ends when the bot leaves or is moved to another channel, which ends `captions` above.
- Tell people before you record or transcribe them. [docs/voice.md][voice-receiving] covers every option, and privacy and consent.

## Helpers

- `bostie.cdn` builds image URLs on Discord's CDN, such as `cdn.display_avatar(user)` for a thumbnail, `cdn.member_avatar(guild_id, user_id, member.avatar)` or `cdn.guild_icon(guild.id, guild.icon, size=256)`. They default to WebP, animated when the hash starts with `a_`, and raise `ValueError` for a size or format the CDN does not serve.
- `bostie.fmt` writes Discord's message formats: `fmt.timestamp(when, "R")` shows "in 5 minutes" in each reader's language and time zone, and `fmt.emoji`, `fmt.command` (a clickable slash command), `fmt.guild_navigation`, `fmt.user`, `fmt.role` and `fmt.channel` build the rest. `fmt.store(application_id, sku_id)` links to the app's store page or one of its SKUs. `fmt.escape_markdown`, `fmt.remove_markdown` and `fmt.escape_mentions` make user text safe to echo, and `fmt.mentioned_users`, `mentioned_roles` and `mentioned_channels` read the ids mentioned in a text.
- `bostie.snowflake_time(id)` says when anything with that id was created, and `bostie.time_snowflake(when)` gives the id of a moment. The `before`, `after` and `around` of a listing that pages by id take a `datetime` as well, so `bot.rest.messages.list(channel_id, after=yesterday)` walks the messages since then.
- `bostie.Locale` lists the languages Discord documents. `ctx.locale` and `Guild.preferred_locale` are `Locale` members, and a language Discord adds later decodes as one too.
- `bostie.permissions.TEXT`, `VOICE` and `STAGE` hold the permissions Discord documents for each kind of channel, and `ELEVATED` those that need two-factor authentication in a guild that requires it, to combine with `|` and `&`.
- `bostie.color` builds the integer colors Discord takes, such as `Embed.color` and a role's colors: `color.rgb(88, 101, 242)`, `color.hsv(0.6, 0.6, 0.9)`, `color.parse("#5865F2")`, and Discord's brand colors such as `color.BLURPLE`.

## Testing

`pip install "bostie[testing]"` adds `bostie.testing`: a fake Discord gateway, REST API and voice server that runs in your test process. bostie's own tests run on it.

```python
from bostie import Bot, Context
from bostie.testing import FakeDiscord, payloads, run, running


def test_ping_answers_pong() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.command
        async def ping(ctx: Context) -> None:
            """Answer pong."""
            await ctx.respond("pong")

        async with running(bot, fake):
            reply = await fake.interaction(2, payloads.command("ping"))
            assert reply.json["data"]["content"] == "pong"

    run(scenario)
```

- `run(scenario)` runs `async def scenario(fake)` against a new `FakeDiscord` within 10 s. `running(bot, fake)` starts the bot, waits until it is ready, and closes it at the end.
- `fake.dispatch(name, d)` sends a gateway event, and `fake.interaction(type, data)` sends an interaction and returns the bot's answer. `payloads` builds both from Discord's documented examples, and `payloads.fixture("events/MESSAGE_CREATE")` returns any of them.
- `fake.requests` records every REST request. `fake.fail(method, route_prefix, status, json)` answers the next matching request with that error, and `fake.hold(method, route_prefix)` keeps it waiting until you set the event it returns.
- `FakeDiscord(voice=True)` adds a voice gateway and a UDP server that decrypts every RTP packet into `fake.packets`, and `fake.move_bot()` and `fake.kick_bot()` move the bot around. It does not speak DAVE.

## Bot parameters

Every parameter of `bostie.Bot` is keyword-only and optional.

- `intents`: the gateway intents. The default, `Intents.DEFAULT`, is every intent except the privileged ones. A privileged intent must also be turned on in the Developer Portal.
- `dev_guilds`: guild ids to sync commands to. When set, commands sync to these guilds only and global commands are left alone. Leave it empty to sync globally.
- `auto_sync`: `False` skips the startup sync. `await bot.sync_commands()` syncs again while the bot is running and returns a `bostie.SyncResult` for each scope, saying what differed.
- `auto_defer`: seconds before an unanswered handler is deferred, or `None` to never defer. `@bot.command(auto_defer=...)` overrides it for one command.
- `cache`: `False` leaves `bot.cache` empty. By default it keeps guilds current, `Guild.roles`, `emojis` and `stickers` included, along with channels, active threads, voice states and the bot's own member in each guild, but no other members. `bot.cache.permissions(channel_id, member)` and `bot.cache.guild_permissions(guild_id, member)` compute a member's permissions from it as Discord does, the bot's own when `member` is left out. `bot.cache.user(id)` finds the bot's user, or the user of a cached member or DM channel. `Guild.member_count` follows joins and leaves only with `Intents.GUILD_MEMBERS`.
- `members`: `True` keeps every member in `bot.cache` too, which needs `Intents.GUILD_MEMBERS`. When a guild arrives without all its members, the bot asks the gateway for the rest, one guild at a time per shard, and the shard is ready once they are in; `chunk_members=False` keeps only the members that events bring. `bot.cache.member(guild_id, user_id)` and `guild_members(guild_id)` read them, and `MemberUpdate.old` and `MemberRemove.member` carry them.
- `presences`: `True` keeps the presences of members who are not offline, which needs `Intents.GUILD_PRESENCES`. `bot.cache.presence(guild_id, user_id)` and `guild_presences(guild_id)` read them, and `PresenceUpdate.old` carries them.
- `max_messages`: how many of the latest messages the cache keeps, 0 by default. `bot.cache.message(id)` reads them, and `MessageUpdate.old`, `MessageDelete.message` and `MessageDeleteBulk.messages` carry them.
- `max_dm_channels`: how many of the latest DM channels the cache keeps, 0 by default: those `bot.rest.channels.create_dm()` opens and those users message the bot in. `bot.cache.dm_channel(user_id)` reads them, so `bot.cache.dm_channel(user_id) or await bot.rest.channels.create_dm(recipient_id=user_id)` asks Discord once.
- `guild_ready_timeout`: seconds a shard waits for the next of its guilds before it counts as ready anyway, 2 by default. Guilds in an outage arrive later.
- `large_threshold`: the member count, 50 to 250 and 50 by default, above which `GuildCreate.members` leaves out offline members without a role, a nickname or a voice channel. Below it, `GuildCreate.members` holds everyone, but only with `Intents.GUILD_PRESENCES`: without it, it holds the members in voice and the bot.
- `allowed_mentions`: who the bot's messages may ping. The default allows users and roles, not `@everyone`. A message's own `allowed_mentions` changes only the kinds of mention it sets, so `AllowedMentions(replied_user=True)` keeps the default for users and roles.
- `status`, `activities`, `afk` and `idle_since`: the presence the bot shows, online with no activity by default. `bostie.activity` builds activities, such as `activity.playing("chess")` or `activity.custom("Rolling dice")`. `await bot.update_presence(...)` changes any of them on every shard, and sessions that start later show the change too.
- `http_settings`: a `bostie.HTTPSettings` with how REST requests are paced, retried and timed out. `global_rate_limit` is the requests per second across all routes, 50 by default; interaction responses and follow-ups do not count. `max_rate_limit_wait` is the longest rate limit bostie waits out before it raises `RateLimited`, 30 s. `retries` is how often a request that is safe to send again is retried after a 5xx or a dropped connection, 2. `connect_timeout` and `read_timeout` are 10 s and 60 s. `proxy` sends REST requests and the gateway connection through an HTTP proxy, such as `"http://user:password@proxy:3128"`; bostie reads no proxy from the environment, and voice connects directly, since its UDP audio cannot go through one. `RESTClient`, `BearerClient`, `WebhookClient` and `SyncWebhookClient` take the same `http_settings=`.
- `api_url`: the REST API base URL, for a rate-limit proxy or a fake Discord in tests. Processes of one bot share Discord's rate limits through a rate-limit proxy such as nirn-proxy or twilight-http-proxy: point `api_url` at it and pass `HTTPSettings(rate_limiter=False)`, so bostie leaves the waiting to the proxy and only waits out a 429 that still comes back.
- `voice`: a `voice.Defaults` with the settings every voice connection and sink starts from.
- `shard_count`: how many shards the bot splits its guilds across. The default, `"auto"`, takes Discord's recommendation at each start, so one process runs every shard it needs.
- `shard_ids`: the shards this process runs, for a bot split across processes. It needs an explicit `shard_count`; the default runs them all. `Started` waits for every shard here, and each event carries its `shard_id`.
- `before_identify`: an async function that gets a shard id before each identify of that shard and returns once it may identify. Processes of one bot can share Discord's `max_concurrency` through it; an exception is logged and the shard identifies anyway.
- `session_file`: a file where `close()` saves each shard's gateway session instead of ending it, and from which the next `start()` resumes them. A restart within a few minutes then gets the events it missed without identifying again, and each shard is ready at its `Resumed`. Discord sends no guilds on a resume, so `bot.cache` starts empty and learns guilds only as they join or a shard starts a new session. The bot shows online until unused sessions time out, and each process of a split bot needs its own file.
- `shutdown_grace`: seconds `close()` gives handlers and listeners still running to finish, once the gateway has closed and before it cancels them, 0 by default. Answers in flight when a deploy sends SIGTERM then still go out. The bot's tasks are cancelled first.

## Errors

A bad argument raises `ValueError` or `TypeError`. Every other error bostie raises, the Rust core's included, is a `bostie.BostieError`:

- `HTTPException` and its subclasses such as `NotFound` when Discord answers with an error status, `TransportError` when a request fails before it answers, and `GatewayClosed` when Discord ends the gateway session for good.
- An `HTTPException` names each field Discord rejected, as in `embeds.0.title: Must be 256 or fewer in length.`, and its `code` is a `JSONErrorCode`, which has every code Discord documents, such as `CANNOT_SEND_MESSAGES_TO_THIS_USER`. An OAuth2 token error reads as its id and description, such as `invalid_grant: Invalid "code" in request.`
- `RateLimited` when Discord answers 429 and asks for a longer wait than `HTTPSettings.max_rate_limit_wait`, or when bostie holds a request back for that reason. Each 429 also logs a warning on `bostie.rest`, and each retry a DEBUG line.
- `NotFound` with the code `UNKNOWN_WEBHOOK` when Discord says a webhook is gone. bostie then sends nothing more to that webhook, as Discord asks, and raises the same error at once.
- `StateError`, also a `RuntimeError`, when an object is not in a state that allows the call, such as `bot.http` or `bot.rest` before `start()`. `ShardNotRunning` and `voice.AlreadyConnected` are kinds of it.
- `MissingIntent`, also a `ValueError`, when a feature needs an intent the bot did not ask for, and `StartupError`, also an `OSError`, when bostie cannot set up a thread, socket, HTTP client or TLS context it needs.
- `CommandError` for commands and interactions, and `voice.VoiceError` for voice.

`@bot.on_error` registers `async def hook(ctx: bostie.ErrorContext, error: Exception)` for what handlers raise, and `@router.on_error(NotFound, Forbidden)` one for those types only, typed `error: NotFound | Forbidden`. Each router from the failing handler's up to the bot offers the hook for the nearest exception class; the first one runs, and a hook that raises the error again passes it to the next. With no hook, the user gets an ephemeral "Something went wrong." The context is the handler's, so an autocomplete error comes with an `AutocompleteContext`, which has no `respond`.

<!-- --8<-- [end:guide] -->

## License

Licensed under either of [Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT), at your option.

bostie is not affiliated with Discord Inc.

[examples]: examples
[running]: docs/running.md
[voice-receiving]: docs/voice.md#receiving-audio
