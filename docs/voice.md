# Voice

## Connecting again

In a guild where the bot is already connected, `bot.voice.connect()` and `ctx.voice.connect()` move that connection, as `vc.move()` does, and return it. The connection keeps its mute, deafen, encoder, speaking flags, speech settings and `server_change_delay`, and the call changes only the options you pass. `timeout` bounds this call alone. The connection asks for its stage role each time it enters a stage channel, through `connect()` or `vc.move()`; a `stage` you pass replaces that role and is requested even when the bot is already there.

## Voice server changes

Discord sometimes moves a call to another voice server, for example when someone changes the channel's region or when a server goes away. Everyone in the call reconnects at the same moment. bostie follows on its own: the connection goes `RECONNECTING`, comes back `READY` on the new server, and the audio held in its sinks plays from there.

A Discord client that is still connecting to the new server when the bot gets there may never play the bot. The bot is connected and sends audio, but that listener hears nothing until the bot connects again. Waiting a moment before connecting to the new server avoids this:

```python
vc = await bot.voice.connect(channel_id, server_change_delay=0.5)
```

or, for every connection:

```python
bot = Bot(voice=voice.Defaults(server_change_delay=0.5))
```

`server_change_delay` is in seconds. `0.5` is enough for the clients we have seen this on. The default, `0`, connects at once, which is fastest when nobody is affected.

The wait applies only to a server change. A first connect and a move to another channel connect at once, and the wait does not count against `timeout`.

## Playing URLs

`voice.Url(url, headers={...})` streams a direct media URL over HTTP and decodes it in Rust. When the connection drops, it reconnects up to three times in a row, after 0.5, 1 and 2 s. A server that takes range requests continues from the byte where it broke; a stream with no length, such as a radio station, continues from its live edge. A sized file on a server without range requests cannot continue, so its track ends with `EndReason.ERROR`, as it does once the three tries fail. A server that sends no length and ends its response by closing the connection looks the same whether it finished or dropped, so that track ends `FINISHED`.

A URL ending in `.m3u8`, or served as an HLS playlist, plays as HLS. A master playlist plays its audio rendition, or its best audio-only variant, or else its smallest one. Segments may be MPEG-TS carrying AAC or MP3, packed AAC or MP3, or fragmented MP4. A live playlist starts three segments from its end and reloads every half target duration until it ends. Encrypted streams and byte ranges are refused with `EndReason.ERROR`, and an HLS track cannot seek.

bostie does not extract media from web pages. A tool such as yt-dlp can: pass the direct URL it finds, with the HTTP headers it says that URL needs, to `voice.Url`.

## Going back in a queue

`voice.Queue(vc, history=20)` keeps the last 20 tracks that played in `queue.history`, oldest first; `voice.Defaults(history=20)` does it for every queue. A queue keeps none by default, because a kept track holds on to its source, and a `voice.Bytes` source to its audio. A track counts once its audio started, whether it finished or was skipped.

`queue.back()` plays the last track of the history again from its start, then the current track from its start, and returns the new track; it returns `None` when the history is empty. The current track ends `STOPPED` and does not go into the history, so calling `back()` again goes further back. Tracks play again through `source.again()`, so a `voice.Stream` cannot: `back()` then raises `voice.SourceError` and leaves the history as it was.

## Soundboard

`await bot.rest.soundboard.send(vc.channel_id, sound_id=...)` plays a soundboard sound in the bot's channel, mixed by Discord with whatever the bot sends; pass `source_guild_id` for a sound from another server. The bot needs the Speak and Use Soundboard permissions, plus Use External Sounds for a sound from another server, and Discord refuses while `vc.deaf`, `vc.self_deaf`, `vc.mute` or `vc.suppress` is true.

## Leaving an empty channel

bostie stays in a channel until you disconnect. To leave once everyone else has gone:

```python
@bot.listen
async def on_leave(event: voice.UserLeave) -> None:
    vc = bot.voice.get(event.guild_id)
    if vc is not None and not vc.members:
        await vc.disconnect()
```

## Voice messages

A voice message is a message with one audio file and nothing else, which Discord shows with a play button and a waveform. `voice.message()` makes that file: it decodes the audio in Rust, off the event loop, and fills in `duration_secs` and a `waveform` of one byte per 100 ms, at most 256 of them, each the loudest sample there. Any format `voice.File` plays works; Discord clients send 48 kHz mono Ogg Opus.

```python
@bot.command
async def greet(ctx: Context) -> None:
    """Answer with a voice message."""
    await ctx.respond(files=[await voice.message("greeting.ogg")])
```

A `bostie.File` with a `waveform` sets `MessageFlags.IS_VOICE_MESSAGE` on the message it goes in. Content, embeds, components, a poll, stickers or a second file raise `ValueError` instead, and so does a waveform without `duration_secs`. Discord does not let anyone edit a voice message.

## Receiving audio

`vc.receive()` opens a receiver: a bounded async iterator over the audio the bot hears in the call. Open as many as you need, at any time. Each one has its own queue, format and settings, and none of them slows the bot's own audio down.

```python
async def listen(vc: voice.VoiceConnection) -> None:
    async with vc.receive(voice.PCM(16_000, 1)) as rx:
        async for frame in rx:
            print(frame.user_id, frame.offset, len(frame.data))
```

Leaving the `async with` block closes the receiver. You can also call `rx.close()`.

### Formats

| Format | What you get |
|---|---|
| `voice.PCM(rate, channels, sample)` | Decoded audio, one `voice.Frame` per user per 20 ms. The rate is 8, 12, 16, 24 or 48 kHz (`voice.DECODE_RATES`), mono or stereo, `"s16le"` or `"f32le"`. The default, `voice.PCM()`, is 48 kHz stereo s16le, the same as a sink's. |
| `voice.Opus()` | Each user's Opus packets as they were sent, in order, one `voice.Frame` per packet, not decoded. This is the cheapest format, made for recording and relaying. |
| `voice.Packets()` | Every packet as it arrives, before any reordering, as a `voice.Packet` with its RTP sequence, timestamp and header extensions. In an end-to-end encrypted call, a packet that waits for its key comes out once it decrypts. |

A `voice.Frame` has these fields:

- `user_id` and `ssrc`: who it is from.
- `offset`: where the frame starts on the receiver's clock. It counts 48 kHz samples from the moment the receiver opened, whatever the output rate, and every user shares it, so frames from different people line up by `offset`.
- `samples`: the frame's length in 48 kHz samples, 960 for 20 ms. An Opus frame is as long as its packet.
- `timestamp`: the RTP timestamp of the packet the frame came from.
- `received_at`: the `time.monotonic()` at which that packet arrived.
- `data`: the PCM bytes, or one Opus packet.
- `lost`: the packet never arrived. A lost PCM frame holds the decoder's best guess at the missing audio. A lost Opus frame has empty `data`: it only marks the gap.
- `level`: how loud a PCM frame is, its RMS in dBFS, measured in Rust: 0 for a full-scale square wave and `-inf` for digital silence. An Opus frame has `None`, since it is not decoded.

A `voice.Packet` has `user_id`, `ssrc`, `sequence`, `timestamp`, `received_at`, `opus` and `extensions`, a tuple of `(id, bytes)` pairs. `dict(packet.extensions)` turns them into a lookup.

### Who to receive

- `users={id, ...}` receives only those users. The default, `None`, receives everyone.
- `exclude={id, ...}` receives everyone except those users.
- `include_self=True` adds the bot's own audio, under the bot's user id, exactly as Discord got it. It runs about `vc.latency.udp / 2` ahead of everyone else's, the time a packet takes to reach Discord. While the bot is alone in an end-to-end encrypted call, it sends nothing, so nothing comes.

Audio from someone no receiver asked for is never decrypted or decoded, so it costs almost nothing.

### Buffer and jitter

- `buffer` is the longest, in seconds, that audio waits for your code: 2 s by default. When your code falls further behind, the oldest audio is dropped (`overflow="drop_oldest"`), or with `overflow="drop_newest"` the queue is kept and the audio arriving is dropped until your code catches up. Either way it is counted in `rx.stats().dropped`. `rx.queued` says how far behind you are, and `rx.clear()` drops everything queued and carries on live.
- `jitter` is how long a frame waits for late or out-of-order packets before it comes out: 0.1 s by default, from 0 to 1 s. A smaller value means less delay and more lost frames. `voice.Packets()` has no jitter: packets come out as they arrive. `await rx.mark()` returns `True` once every packet the bot received before the call, and that this receiver takes, has come out into the queue or been dropped on the way, and `False` if the receiver ends first or that takes over 1.2 s plus twice the jitter.
- `stall="wait"` keeps the receiver open while the connection reconnects. Nothing comes out meanwhile, and the gap shows in `offset`. With `stall="end"`, the receiver ends instead, with `voice.ClosedReason.RECONNECTING`, as soon as the connection starts reconnecting, or at once if it opens while the connection reconnects. A resume does not end it.
- `Bot(voice=voice.Defaults(receive_buffer=..., jitter=..., overflow=..., receive_stall=...))` changes the defaults for every receiver.

When audio reaches the bot but nothing comes out of any receiver for 2 s, for example because it cannot be decrypted, `voice.MediaStalled` fires with `direction=voice.Direction.RECEIVE`, and `voice.MediaResumed` fires when audio comes out again.

### Speech

`voice.SpeechStart` and `voice.SpeechStop` events fire for everyone in the call, with or without a receiver. Their `at` is the `time.monotonic()` of the first speech packet, or of the end of the last one, and `SpeechStop.duration` is how long the user spoke. Someone counts as stopped once they have sent no speech for `speech_hangover` seconds, 0.2 by default. In an end-to-end encrypted call, a stop can come up to 0.1 s late for someone no receiver is receiving, or whose audio cannot be decrypted yet. Change it with `connect(speech_hangover=...)`, `voice.Defaults(speech_hangover=...)` or `vc.speech_hangover = ...` on a live connection.

This reply stops as soon as anyone starts talking:

```python
replies: dict[int, voice.Track] = {}


@bot.command
async def say(ctx: Context, path: str) -> None:
    """Play a reply that stops as soon as someone starts talking."""
    vc = ctx.voice.current or await ctx.voice.connect()
    replies[vc.guild_id] = await vc.play(voice.File(path))
    await ctx.respond("speaking")


@bot.listen
async def on_speech(event: voice.SpeechStart) -> None:
    if (track := replies.pop(event.guild_id, None)) is not None:
        track.stop()
```

`vc.speaking` maps the users talking now to the time they started, and `vc.ssrcs` maps each SSRC in the call to its user id.

With `markers=True`, a PCM or Opus receiver also yields `voice.SpeechStart` and `voice.SpeechStop` in its own stream, in order with the frames. Their `offset` is set, on the receiver's clock, so you know exactly which frames each utterance covers. A `voice.Packets()` receiver yields them in arrival order, with `offset` set to `None`.

A bot that never listens, such as a music bot, can turn speech activity off with `connect(speech_activity=False)`, `voice.Defaults(speech_activity=False)` or `vc.speech_activity = False`. Then no `voice.SpeechStart` or `voice.SpeechStop` event fires and `vc.speaking` stays empty; turning it off ends the speech in progress with a `voice.SpeechStop`. Receivers work as before, `markers=True` included, and so do `vc.ssrcs` and the receive `voice.MediaStalled` events. While speech activity is off and no receiver is open, the bot does not even decrypt the audio it gets, which saves most of the cost of receiving.

### Mixing

`mix=True` turns a PCM receiver into one continuous stream: everyone it receives, summed, one frame per 20 ms, silence included. A mixed frame has no `user_id`, `ssrc` or `timestamp`. A mix cannot have markers. Because both ends default to 48 kHz stereo s16le, a mix relays straight into another call:

```python
async def relay(source: voice.VoiceConnection, target: voice.VoiceConnection) -> None:
    async with source.receive(voice.PCM(), mix=True) as rx, target.sink() as out:
        async for frame in rx:
            await out.write(frame.data)
```

### Relaying Opus

An Opus receiver relays one speaker without decoding anything. Skip the lost frames: they hold no packet, and an Opus sink accepts only real packets.

```python
async def forward(
    source: voice.VoiceConnection, target: voice.VoiceConnection, user_id: int
) -> None:
    async with (
        source.receive(voice.Opus(), users={user_id}) as rx,
        target.sink(voice.Opus()) as out,
    ):
        async for frame in rx:
            if not frame.lost:
                await out.write(frame.data)
```

### Utterances

`voice.utterances(rx)` groups a PCM receiver's frames into a `voice.Utterance` per user and stretch of speech, which is what speech-to-text engines want. The receiver needs `markers=True`. An utterance ends when no new speech follows within `min_silence` seconds (0.8 by default), or at `max_length` seconds (30 by default), where the rest carries on as the next one. Short pauses inside an utterance are filled with silence, so the audio keeps its timing.

```python
from bostie import AllowedMentions


async def captions(vc: voice.VoiceConnection, channel_id: int) -> None:
    async with vc.receive(voice.PCM(16_000, 1), markers=True) as rx:
        async for said in voice.utterances(rx, min_silence=0.6):
            if said.lost < said.duration / 2:
                text = await speech_to_text(said.data)
                await bot.rest.messages.create(
                    channel_id,
                    f"<@{said.user_id}>: {text}",
                    allowed_mentions=AllowedMentions(parse=()),
                )
```

Here `speech_to_text` stands for your engine: it gets 16 kHz mono 16-bit audio. An utterance has `user_id`, `data`, `offset`, `duration` and `lost`, the seconds of it that are the decoder's guesses at lost packets. Skipping utterances that are mostly guesses keeps speech-to-text engines from inventing words.

Speech markers come from packet activity: someone whose client sends audio all the time, such as a microphone left open on a noisy fan, never stops speaking. `voice.utterances(rx, level=-45.0)` cuts by loudness instead: a frame quieter than `level` dBFS counts as silence, and the receiver needs no markers. Pick the level by printing `frame.level` while people talk and while they do not.

### Recording

`voice.Recorder` writes one file per user as the call goes, and never keeps a recording in memory:

```python
recorders: dict[int, voice.Recorder] = {}


@bot.command
async def record(ctx: Context) -> None:
    """Record everyone, the bot included, to one Ogg Opus file each."""
    vc = ctx.voice.current or await ctx.voice.connect()
    recorders[vc.guild_id] = voice.Recorder(
        vc, "recordings/{guild_id}/{user_id}.ogg", include_self=True
    )
    await ctx.respond("Recording. Everyone in this call can see the bot.")


@bot.command
async def stop(ctx: Context) -> None:
    """Stop recording and say how many files were written."""
    recorder = recorders.pop(ctx.guild_id, None) if ctx.guild_id else None
    files = await recorder.stop() if recorder is not None else {}
    await ctx.respond(f"{len(files)} files")
```

- The path must contain `{user_id}`. `{guild_id}` is the only other field. Missing directories are created.
- A path ending in `.ogg` or `.opus` gets Ogg Opus, the packets as they came. A path ending in `.wav` gets 16-bit PCM WAV, decoded in Rust, in the `format=voice.PCM(rate, channels)` you choose, 48 kHz stereo by default; that is what editors and speech-to-text tools take, at about 190 KB a second per user at the default format.
- `mix=True` records the whole call to one file, everyone summed with silence in between, so the path takes no `{user_id}`. An Ogg Opus mix is encoded once in Rust. Its file is under `None` in `recorder.files` and in what `stop()` returns.
- With `align=True`, the default, every file starts when the recording started and silence fills every gap, so the files line up when you play them together, to within about 10 ms plus the network's jitter. The bot's own file runs slightly ahead, by the time its packets take to reach Discord. `align=False` keeps only the speech.
- A recorder's receiver waits longer than a live one: `buffer=30.0` so a slow disk loses nothing, and `jitter=0.3` so late packets still make it in. `users`, `exclude` and `include_self` work as they do for `vc.receive()`.
- `recorder.stop()` first waits for the audio heard before the call, as `rx.mark()` does, then writes what is queued, finishes every file and returns them by user id. If that wait gives up, the files still finish, without the audio that had not come out. `async with voice.Recorder(...) as recorder:` stops it when the block ends. If writing failed, for example on a full disk, `stop()` raises that error. When the connection ends or the bot is moved, the recorder finishes its files on its own, and `stop()` still returns them. It keeps recording through a reconnect, whatever `receive_stall` says. `recorder.files` lists the files written so far, and `recorder.stats()` the receiver's counters.
- `max_duration=600.0` ends the recording on its own 600 s after it started, and `max_size=25_000_000` once its files hold that many bytes in all. Audio past `max_duration` is left out. The write that reaches `max_size` still goes in whole, so the files can end over it by up to one Ogg page, about a second of one person's audio, or one WAV frame. `await recorder.wait()` waits until the recording ends on its own, at a limit or when the connection ends or the bot is moved, and returns the files as `stop()` does.

### Stats

`rx.stats()` returns a `voice.ReceiverStats`: frames and packets `delivered`, `dropped` because your code fell behind, and `lost` on the way.

### When a receiver ends

`rx.closed_reason` is `None` while the receiver is open, then a `voice.ClosedReason`:

- `CLOSED`: you closed it, or left the `async with` block. Anything still queued is dropped.
- `DISCONNECTED`: the connection ended.
- `MOVED`: the bot was moved to another channel, by `vc.move()` or by a moderator. Read Privacy and consent below before you open a new one there.
- `RECONNECTING`: the connection started reconnecting and the receiver was opened with `stall="end"`.

After `DISCONNECTED`, `MOVED` and `RECONNECTING`, the audio already queued is still yours to read, and then `async for` ends. `rx.get_nowait()` returns the next item without waiting. It raises `voice.Empty` when nothing is queued, and `voice.ReceiverClosed` once the receiver has ended and everything was read.

A bot that a moderator deafened hears nothing: Discord stops sending it audio until it is undeafened. `vc.deaf` tells you whether that is the case, and bostie logs a warning when you open a receiver then, or when a moderator deafens the bot while a receiver is open. The bot's own deafen (`self_deaf=True`) only shows the icon in the client: Discord keeps sending it audio.

### Privacy and consent

- In an end-to-end encrypted call, the bot is a member of the call's encryption group. Discord clients list it, and the call's privacy code changes when it joins. People can check it with the code from `await vc.dave.verification_code(user_id)`. A bot cannot listen without being seen.
- Discord gives bots no signal that people agreed to be recorded. bostie cannot ask for you. Tell people before you record or transcribe them, by announcing it or by setting the voice channel status, and stop when someone objects.
- Receive only the people you need. `users=` and `exclude=` limit a receiver, and audio that no receiver asked for is never decrypted or decoded.
- A receiver, and so a `Recorder`, ends when the bot is moved to another channel. The people there did not agree to it. Open a new one once they have.
- Recording laws differ from place to place, and some require everyone's consent. Read Discord's [Developer Terms of Service](https://support-dev.discord.com/hc/articles/8562894815383-Discord-Developer-Terms-of-Service) and [Developer Policy](https://support-dev.discord.com/hc/articles/8563934450327-Discord-Developer-Policy) before you store or share voice data. Keep recordings only as long as you need them, and store them securely.

Setting the voice channel status is one request. The bot needs the Set Voice Channel Status permission:

```python
await bot.rest.channels.set_voice_status(vc.channel_id, status="Recording")
```
