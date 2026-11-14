# Receiving and recording

`vc.receive()` opens a receiver: a bounded async iterator over the audio the bot hears. Each speaker arrives on their own, decrypted, put back in order and decoded in Rust. Several receivers can be open on one connection, each with its own format and filters.

```python
--8<-- "docs/snippets/voice/listening.py"
```

## Formats

`vc.receive(format)` returns a `voice.Receiver` for a `voice.ReceiveFormat`:

| `vc.receive(format)` | Items | For |
|---|---|---|
| `voice.PCM(rate=48000, channels=2, sample="s16le")` | `Frame`s of decoded samples in that format; the rate is one of `voice.DECODE_RATES`, 8, 12, 16, 24 or 48 kHz | listening, speech recognition, levels |
| `voice.Opus()` | `Frame`s whose `data` is one Opus packet | recording or forwarding without decoding |
| `voice.Packets()` | `Packet`s, decrypted, in arrival order, before the jitter buffer | your own jitter buffer, RTP header extensions |

A `Frame` has the `user_id`, the `ssrc`, the RTP `timestamp`, `offset` and `samples` on the receiver's 48 kHz clock, `received_at`, the `data`, `lost` (true for audio that Opus made up to hide a lost packet), and for PCM the `level`, the frame's RMS in dBFS. The clock starts when the receiver opens, so frames of different users line up by `offset`. A `Packet` has the `user_id`, `ssrc`, `sequence`, `timestamp`, `received_at`, the `opus` payload and the RTP header `extensions`.

## Choosing whom to hear

- `users=[...]` keeps only those users, and `exclude=[...]` drops some.
- `include_self=True` adds the bot's own audio.
- `mix=True` (PCM and Opus) delivers one stream of everyone mixed, with `user_id` set to `None`.
- `markers=True` puts `SpeechStart`, `SpeechStop` and `UserLeave` events between the frames, each with its `offset` on the same clock, so a consumer knows where speech starts and ends without timing it.

## Settings

The settings are the `voice.ReceiveOptions` of `vc.receive`, or `voice.PacketReceiveOptions` for `Packets()`, which has no jitter buffer.

| Setting | Default | Meaning |
|---|---|---|
| `buffer` | `Defaults.receive_buffer`, 2.0 | seconds of items the receiver holds for a slow consumer, 0.02 to 60 |
| `overflow` | `Defaults.overflow`, `"drop_oldest"` | when the buffer is full: drop the oldest items, or `"drop_newest"` |
| `jitter` | `Defaults.jitter`, 0.1 | seconds to wait for late packets before playing on, 0 to 1; PCM and Opus only |
| `stall` | `Defaults.receive_stall`, `"wait"` | when the connection reconnects: `"wait"` keeps the receiver open, `"end"` ends it |

## Reading

`async for item in rx` waits for items and stops when the receiver ends. `async with rx:` closes it when the block ends.

- `rx.get_nowait()` returns the next item, or raises `voice.Empty` when nothing is queued and `voice.ReceiverClosed` when the receiver ended.
- `rx.queued` is how long the oldest queued item has waited: the consumer's lag.
- `rx.clear()` drops everything queued and continues live.
- `await rx.mark()` returns `True` once the audio received before the call is queued or dropped, or `False` after 1.2 seconds plus twice the jitter.
- `rx.stats()` returns a `ReceiverStats`: items `delivered`, `dropped` by overflow, and packets `lost` on the way.
- `rx.close()` stops receiving. `rx.closed_reason`, a `voice.ClosedReason`, says why a receiver ended: `CLOSED`, `DISCONNECTED`, `MOVED` (the bot changed channels) or `RECONNECTING` (with `stall="end"`).

A moderator can deafen the bot, and Discord then sends it no audio; bostie logs a warning when a receiver opens while the bot is deafened, and when the bot is deafened while receivers are open.

## Relaying

A receiver and a sink connect directly, to carry audio from one call to another:

```python
--8<-- "docs/snippets/voice/relay.py"
```

A mixed frame has no `user_id`, `ssrc` or `timestamp`, and a mix cannot carry markers. A lost Opus frame has empty `data`, because there is no packet to pass on; skip it before writing to an Opus sink, as `relay_speaker` does. Both ends default to 48 kHz stereo 16-bit PCM, so a PCM receiver writes straight into a default sink.

## Speech

Speech detection runs in Rust on every connection while `speech_activity` is on: a user starts speaking with their first audio and stops after `speech_hangover` seconds of silence, 0.2 by default. `vc.speaking` holds who speaks now, the `SpeechStart` and `SpeechStop` events report it to listeners, and receivers opened with `markers=True` carry it in their stream. With `speech_activity` off and no receiver open, bostie does not even decrypt the incoming audio.

`voice.utterances(rx, min_silence=0.8, max_length=30.0)` groups a PCM receiver's frames into `Utterance`s: one user's speech from start to end, with `data` in the receiver's format, its `offset`, `duration` and the seconds `lost`. An utterance ends after `min_silence` seconds without speech, or at `max_length` seconds. It needs a PCM receiver without `mix`, opened with `markers=True`, or a `level` in dBFS, which then decides what counts as speech instead of the markers.

## Recording

```python
--8<-- "docs/snippets/voice/call_recording.py"
```

`voice.Recorder(vc, path)` records each user to their own file, or with `mix=True` the whole call to one. The path is a template with `{guild_id}` and `{user_id}`, required for one file per user and refused with `mix=True`, and its extension picks the container:

- `.ogg` or `.opus` writes Ogg Opus. Per user, the received Opus packets are written as they are, without decoding.
- `.wav` writes 16-bit PCM in the recorder's `format`, 48 kHz stereo by default; its rate is one of `voice.DECODE_RATES`.

With `align=True`, the default, silence fills the gaps, so every file starts when the recording did and the files line up. `users`, `exclude` and `include_self` choose whom to record, as for a receiver, and `buffer` (30 seconds) and `jitter` (0.3 seconds) are larger than a receiver's defaults, because a disk can stall. The recording ends on its own after `max_duration` seconds, or once its files hold `max_size` bytes, or when the connection ends.

`await recorder.stop()` ends it once the audio already heard is written, and returns the files as a dict from user id to `Path`, with `None` as the key of a mixed file. `await recorder.wait()` waits for it to end on its own. `recorder.files` lists the files written so far, and `recorder.stats()` the receiver's counters, where `dropped` means the disk fell behind for longer than `buffer`.

Tell the people in the call that the bot records them, as the snippet does.
