# Voice in bostie

bostie's voice engine runs in Rust: decoding, mixing, Opus, transport encryption and DAVE, Discord's end-to-end encryption, which needs no setting and no extra package. Voice needs `Intents.GUILD_VOICE_STATES`, which `Intents.DEFAULT` holds.

## Join, play, stop

```python
import bostie
from bostie import ComponentContext, Context, ui, voice

bot = bostie.Bot()


@bot.command
async def play(ctx: Context, url: str) -> None:
    """Play a direct media URL in your voice channel."""
    vc = ctx.voice.current or await ctx.voice.connect()
    await vc.play(voice.Url(url))
    stop = ui.button("Stop", custom_id=stop_playing.id(guild_id=vc.guild_id))
    await ctx.respond(components=[ui.text(f"Playing <{url}>"), ui.row(stop)])


@bot.on_component("stop:{guild_id:int}")
async def stop_playing(ctx: ComponentContext, guild_id: int) -> None:
    if (vc := bot.voice.get(guild_id)) is not None:
        vc.stop()
    await ctx.update(components=[ui.text("Stopped.")])


bot.run()
```

- `ctx.voice.current` is the bot's connection in the guild, or `None`. `await ctx.voice.connect()` joins the voice or stage channel of the user who ran the command and raises `voice.NotInVoice` when they are in none. Outside a handler, `await bot.voice.connect(channel_id)` joins by id and `bot.voice.get(guild_id)` returns a connection. The bot has one connection per guild. Both `connect` calls take keyword settings such as `self_deaf=True`, `timeout=20` or `stage="speak"`; one left out keeps the bot's `voice.Defaults`.
- `await vc.play(source)` returns a `voice.Track` at once and keeps playing after the handler returns. Tracks that play at the same time are mixed.
- Sources: `voice.File(path)`, `voice.Url(url)` for a direct media URL or an HLS playlist (not a web page that embeds the media), `voice.Bytes(data)`, `voice.Stream(items, format)` for your own PCM or Opus, and `voice.FFmpeg(args)`, the only one that needs `ffmpeg` on `PATH`. A source plays once; `source.again()` returns a fresh copy.
- On a track: `await track.wait()` waits for its end and returns a `voice.TrackEnd`; `track.pause()`, `track.resume()`, `track.stop()`, `await track.seek(seconds)`, and `track.volume`, which changes at once.
- `vc.stop()` ends every track, and `await vc.disconnect()` leaves the channel. `bot.close()` leaves every channel.

## Queues

```python
import bostie
from bostie import Context, voice

bot = bostie.Bot(voice=voice.Defaults(crossfade=3.0, history=20))
queues: dict[int, voice.Queue] = {}


async def queue_of(ctx: Context) -> voice.Queue:
    vc = ctx.voice.current or await ctx.voice.connect()
    queue = queues.get(vc.guild_id)
    if queue is None or queue.closed:
        queue = queues[vc.guild_id] = voice.Queue(vc)
    return queue


@bot.command
async def add(ctx: Context, url: str, next_up: bool = False) -> None:
    """Add a URL to the queue, or play it next."""
    queue = await queue_of(ctx)
    queue.add(voice.Url(url), meta=ctx.user.id, index=0 if next_up else None)
    await ctx.respond(f"{len(queue.upcoming)} tracks up next.")


@bot.command
async def queue(ctx: Context) -> None:
    """Show what plays now and next."""
    player = await queue_of(ctx)
    lines = [f"Now: {player.now.source!r}" if player.now else "Nothing playing"]
    lines += [f"{i}. {track.source!r}" for i, track in enumerate(player.upcoming, 1)]
    await ctx.respond("\n".join(lines))


@bot.command
async def back(ctx: Context) -> None:
    """Play the previous track again."""
    player = await queue_of(ctx)
    previous = player.back()
    await ctx.respond("Going back." if previous else "Nothing played before.")


@bot.command
async def shuffle(ctx: Context) -> None:
    """Shuffle the upcoming tracks."""
    (await queue_of(ctx)).shuffle()
    await ctx.respond("Shuffled.")
```

- `voice.Queue(vc)` plays tracks one after another. `queue.add(source)` returns the `voice.Track`; `queue.skip()`, `queue.back()`, `queue.shuffle()` and `queue.clear()` change what plays; `queue.loop = "queue"` repeats.
- A queue whose connection ended is `closed` and takes no more tracks. Make a new one, as `queue_of` does above.

## Listening and recording

- `voice.Recorder(vc, "recordings/{guild_id}/call.wav", mix=True, max_duration=300)` records the call to a WAV file, and `await recorder.wait()` returns the files when it stops. Tell the people in the call that the bot records them.
- The events of a call come through `@bot.listen` and live in `bostie.voice`: `voice.UserJoin`, `voice.UserLeave`, `voice.SpeechStart`, `voice.SpeechStop`, `voice.StateChange`.

## Errors

- `voice.VoiceConnectError` with reason `TIMEOUT`: Discord did not answer the join, usually because the channel is full or the bot lacks the Connect permission.
- `voice.VoiceNotConnected`: a call on a connection that ended.
- `voice.SourceError`: a source that cannot be opened, decoded or seeked.
