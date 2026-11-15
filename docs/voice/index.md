# Voice connections

bostie's voice engine runs in its Rust core: the voice WebSocket, the UDP transport, the 20 ms send clock, decoding and resampling, the mixer, Opus encoding and decoding, transport encryption, and DAVE end-to-end encryption. Python asks it to join, play and receive; the timing never depends on the event loop, except that Python has to keep each sink's buffer filled.

Voice needs `Intents.GUILD_VOICE_STATES`, which `Intents.DEFAULT` includes. The bot has at most one voice connection per guild.

```python
--8<-- "docs/snippets/voice/voice_connection.py"
```

## Connecting

`await ctx.voice.connect()` joins the voice or stage channel of the user who ran the command, and returns once audio can flow both ways. It finds the user's channel in the cache, or asks Discord when the guild is not cached, and raises `NotInVoice` when the user is in no voice channel of the guild or the interaction came from a DM. `ctx.voice.current` is the bot's connection in that guild, or `None`.

Outside a handler, `await bot.voice.connect(channel_id)` joins a channel by id. In a guild where the bot is already connected, it moves the connection and applies only the options given. `bot.voice.get(guild_id)` and `bot.voice.connections` return the open connections, and `await bot.voice.close()` leaves them all, as `bot.close()` does.

A join that is not ready within `timeout` seconds raises `VoiceConnectError` with reason `TIMEOUT`; Discord leaves a join to a full channel, or one the bot may not connect to, unanswered. Other failures raise `VoiceConnectError` with the reason, the close code and Discord's message.

## Settings

`Bot(voice=voice.Defaults(...))` sets what every connection starts with, and `connect()` takes the connection settings for one call:

| `voice.Defaults` field | Default | Meaning |
|---|---|---|
| `self_mute`, `self_deaf` | `False` | the bot's own mute and deafen |
| `timeout` | 10.0 | seconds to wait for a join, a move or an edit |
| `server_change_delay` | 0.0 | seconds to wait before following Discord to a new voice server |
| `encoder` | `EncoderSettings()` | the Opus encoder; see [Playing audio](playback.md#encoder) |
| `speaking_flags` | `MICROPHONE` | the `voice.SpeakingFlags` the bot announces when it plays |
| `stage` | `"listen"` | on a stage: `"listen"`, `"request"` to ask to speak, or `"speak"` |
| `speech_hangover` | 0.2 | seconds of silence before a speaker counts as stopped, 0.02 to 10 |
| `speech_activity` | `True` | whether speech start and stop events fire |
| `stall_after` | 2.0 | seconds media may stop flowing before `MediaStalled` reports it, at least 0.02 |
| `sink_buffer`, `stall` | 0.2, `"hold"` | the defaults of every [sink](sinks.md) |
| `receive_buffer`, `jitter`, `overflow`, `receive_stall` | 2.0, 0.1, `"drop_oldest"`, `"wait"` | the defaults of every [receiver](receiving.md) |
| `crossfade`, `history` | 0.0, 0 | the defaults of every [queue](playback.md#queues) |

`connect()` takes `self_mute`, `self_deaf`, `timeout`, `server_change_delay`, `encoder`, `speaking_flags`, `stage`, `speech_hangover`, `speech_activity` and `stall_after`. Invalid values raise `ValueError` when the `Defaults` is built. For annotations, the options of `connect()`, `move()`, `edit()` and `connect_external()` are the typed dictionaries `voice.ConnectOptions`, `voice.MoveOptions`, `voice.EditOptions` and `voice.ExternalConnectOptions`, and the string settings have the literal types `voice.StageRole`, `voice.SinkStall`, `voice.ReceiveStall`, `voice.Overflow`, `voice.Sample`, `voice.OpusApplication`, `voice.OpusBandwidth` and `voice.OpusSignal`. `ctx.voice` is a `voice.ContextVoice` and `bot.voice` a `voice.VoiceManager`.

## The connection

| `VoiceConnection` | Value |
|---|---|
| `guild_id`, `channel_id` | where it is |
| `state` | a `voice.State`: `CONNECTING`, `READY`, `RESUMING`, `RECONNECTING` or `DISCONNECTED` |
| `members` | the ids of the other users in the call |
| `speaking` | the users speaking now, mapped to the `time.monotonic()` they started |
| `ssrcs` | each SSRC in the call mapped to its user id |
| `self_mute`, `self_deaf` | the bot's own mute and deafen, as Discord applied them |
| `mute`, `deaf`, `suppress` | whether a moderator muted or deafened the bot, or it may not speak on this stage |
| `latency` | a `Latency` with the WebSocket and UDP round trips in seconds, `None` until measured |
| `dave` | the call's end-to-end encryption; see below |
| `sinks` | the open sinks, oldest first |
| `encoder`, `speaking_flags`, `speech_hangover`, `speech_activity`, `stall_after` | settable while connected |

- `await vc.move(channel_id)` moves to another voice or stage channel of the guild and returns once media flows there.
- `await vc.edit(self_mute=..., self_deaf=...)` changes the bot's own mute or deafen and returns once Discord applied it.
- `await vc.stage("request")` asks to speak on a stage, `"speak"` speaks (with the permission to), `"listen"` goes back to the audience.
- `await vc.wait_until(voice.State.READY)` waits for a state, and raises `VoiceNotConnected` if the connection ends first.
- `vc.reconnect()` drops the voice WebSocket and resumes on a new one.
- `vc.stop()` closes every sink, so nothing plays.
- `await vc.disconnect()` leaves the channel and returns once the connection ended, at most 2 seconds later.

Calls on a connection that ended raise `VoiceNotConnected`.

The connection heals itself. A dropped WebSocket resumes, a lost session reconnects, and when a moderator moves the bot, it follows to the new channel and sends a `Moved` event. When a moderator disconnects it or deletes the channel, it ends with reason `REMOVED`. bostie warns when the bot plays while server muted or suppressed on a stage, and when receivers wait while it is deafened.

## End-to-end encryption

bostie speaks DAVE, Discord's end-to-end encryption for voice, with nothing to set up: it joins the call's encryption group, encrypts what it sends and decrypts what it receives. `vc.dave` reports it as a `voice.Dave`:

- `protocol_version` is the DAVE protocol in use, 0 for none;
- `epoch` counts the changes of the group's members;
- `privacy_code` is the code Discord clients show for the call;
- `await vc.dave.verification_code(user_id)` returns the code to compare with another user out of band, to verify that nobody else is in the middle.

A `DaveTransition` event reports each change. A channel that requires DAVE from a client that does not offer it closes with reason `DAVE_REQUIRED`, and a failed DAVE step raises `DaveError` or ends the connection with `DAVE_FAILED`.

## Events

Voice connections send their events through `@bot.listen`. Each is a `voice.VoiceEvent` with the `guild_id`. A `StateChange` into `DISCONNECTED` carries a `voice.DisconnectReason`: `REQUESTED` (`disconnect()` ran), `REMOVED` (a moderator disconnected the bot or deleted the channel), `CALL_TERMINATED`, `RATE_LIMITED`, `TIMEOUT`, `SESSION_INVALID`, `AUTHENTICATION_FAILED`, `DAVE_REQUIRED`, `DAVE_FAILED`, `PROTOCOL_ERROR` or `CLOSED` (the bot closed).

| Event | Fields | Sent when |
|---|---|---|
| `StateChange` | `old`, `new`, `reason`, `code`, `detail` | the connection changed state |
| `Moved` | `old_channel_id`, `new_channel_id`, `by_us` | the bot moved, by `move()` or by a moderator |
| `DaveTransition` | `protocol_version`, `epoch`, `transition_id` | the end-to-end encryption changed |
| `MediaStalled`, `MediaResumed` | `direction` (`voice.Direction.SEND` or `RECEIVE`), `duration` | sending or receiving stopped, or started again |
| `UserJoin`, `UserLeave` | `user_id` | a user joined or left the call |
| `SpeakingUpdate` | `user_id`, `ssrc`, `flags` | a user announced their SSRC and speaking flags |
| `SpeechStart`, `SpeechStop` | `user_id`, `ssrc`, `at`, and `duration` on stop | a user started or stopped speaking |
| `RtcpPacket` | `packet_type`, `count`, `ssrc`, `user_id`, `reports`, `data` | the voice server sent an RTCP packet; each `ReceptionReport` in `reports` has the `fraction_lost`, `lost`, `highest_sequence`, `jitter`, `last_sender_report` and `delay` of one stream |

They live in `bostie.voice`. `SpeechStart` and `SpeechStop` fire only while `speech_activity` is on.

## Stalled media

```python
--8<-- "docs/snippets/voice/media_stalls.py"
```

`MediaStalled` with `direction` `SEND` fires when audio waits in a sink but cannot leave for `stall_after` seconds, during a reconnect or a DAVE transition. With `RECEIVE`, packets reach an open receiver but none can be opened or played for that long. `MediaResumed` follows once media flows again, its `duration` counting from the start of the stall. `stall_after` is 2 seconds unless `voice.Defaults`, `connect(stall_after=...)` or `vc.stall_after` says otherwise.

## Leaving an empty channel

The bot stays in a channel until it disconnects. To leave when the last user does:

```python
--8<-- "docs/snippets/voice/leave_when_alone.py"
```

## Errors

Voice errors are `VoiceError`s: `VoiceConnectError` (a join ended before it was ready; `reason`, `code`, `detail`), `NotInVoice`, `VoiceNotConnected`, `AlreadyConnected`, `VoiceQueueFull` (too many commands waiting for the connection), `DaveError`, and for audio `SourceError`, `SinkClosed`, `BufferFull`, `Empty` and `ReceiverClosed`, which the next pages explain.

## External voice engines

```python
--8<-- "docs/snippets/voice/external_engine.py"
```

`await bot.voice.connect_external(guild_id, channel_id)` joins a channel for a voice engine that runs elsewhere, and returns an `ExternalVoice` with the `state` (`VoiceStateUpdate`) and `server` (`VoiceServerUpdate`) the engine connects with. bostie then sends no audio in that guild. `async with external.updates() as updates:` delivers the guild's later voice servers and the bot's own voice states, and `await external.disconnect()` leaves the channel. It raises `AlreadyConnected` in a guild where bostie runs its own connection, and takes `self_mute`, `self_deaf` and `timeout`.

## Versions

`voice.versions()` returns the versions of bostie, libopus, the DAVE library and the highest DAVE protocol compiled into the build.
