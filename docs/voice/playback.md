# Playing audio

`await vc.play(source)` plays a source on a [voice connection](index.md) and returns a `Track` at once. Several tracks play at the same time and are mixed; a `Queue` plays them one after another.

```python
--8<-- "docs/snippets/voice/tracks.py"
```

## Sources

A source is audio to play. Each is used once: `play` opens it, and `source.again()` returns a new one with the same audio, for playing it twice.

| Source | Plays | Seekable |
|---|---|---|
| `voice.File(path)` | a local file | yes |
| `voice.Bytes(data, hint=None)` | file contents in memory; `hint` is an extension such as `"ogg"` | yes |
| `voice.Url(url, headers=None)` | a direct HTTP or HTTPS media URL, streamed, or an HLS playlist | when the server accepts range requests; HLS no |
| `voice.FFmpeg(args, executable="ffmpeg")` | the output of an ffmpeg command, for formats bostie does not decode | no |
| `voice.Stream(items, format)` | an async iterable of PCM chunks or Opus packets in `format` | no |

`File`, `Bytes` and `Url` are decoded in Rust: Ogg (Opus, Vorbis), WebM and Matroska, MP4 and M4A (AAC), MP3, FLAC and WAV. A URL must point at the media itself, not at a web page that embeds it. When the connection to the server breaks, `Url` reconnects up to three times, after 0.5, 1 and 2 seconds, and continues where it broke if the server accepts range requests. An HLS master playlist plays its audio rendition, a live playlist starts three segments before its end, and encrypted playlists and byte-range segments are refused with `SourceError`. `FFmpeg` runs the program with your input arguments and reads 48 kHz stereo 16-bit PCM from its output; it raises `SourceError` when the program is not on `PATH` or exits with an error. A source that cannot be opened, decoded or seeked raises `SourceError`, or ends its track with reason `ERROR`.

After `open()`, which sets `opened`, a source has `format`, `duration` (seconds, `None` when unknown), `seekable` and `tags`, a `voice.Tags` with the `title`, `artist` and `album` the file names.

```python
--8<-- "docs/snippets/voice/other_sources.py"
```

Opus audio at 48 kHz stereo in 20 ms packets, such as most Ogg Opus and WebM files, is sent as it is, without decoding and encoding again, while it plays alone at volume 1 without filters. `track.passthrough` says when that happens.

## Tracks

`vc.play(source, volume=1.0, filters=Filters(), meta=None)` takes the starting volume and filters, and `meta`, any value to keep with the track, such as who asked for it. It also takes `buffer` and `stall` for the track's [sink](sinks.md).

| `Track` | Does |
|---|---|
| `await track.wait()` | waits for the end and returns a `TrackEnd`; cancelling the wait does not stop the track |
| `await track.started()` | waits until it starts playing; `False` if it ended first |
| `track.ended` | the `TrackEnd`, or `None` while it plays |
| `track.position` | seconds of the source played so far |
| `track.pause()`, `track.resume()`, `track.paused` | stop and restart feeding it; the audio already buffered still plays |
| `await track.seek(seconds)` | continue from a position; `SourceError` when the source cannot seek |
| `track.volume`, `track.filters` | change at once while it plays |
| `track.stop()` | end it |
| `track.source`, `track.meta`, `track.sink` | what it plays, your value, and the sink it writes to |

A `TrackEnd` has the `track`, the `reason` (`FINISHED`, `STOPPED`, `ERROR` with the `error`, or `CLEANUP` when the connection ended) and `may_start_next`, true after `FINISHED` or `ERROR`, which says whether a player of your own should go on to its next track. `vc.stop()` ends every track.

## Volume and filters

`volume` multiplies the samples: 0.5 is half as loud, 1.0 leaves them unchanged. `voice.Filters` sets effects that Rust applies as the audio plays:

| `Filters` field | Default | Range |
|---|---|---|
| `equalizer` | `{}` | up to 16 bands, mapping a centre frequency from 20 to 20 000 Hz to a gain in dB; each band is an octave wide |
| `bass` | 0.0 | gain in dB below 100 Hz |
| `speed` | 1.0 | 0.5 to 2.0, keeping the pitch |
| `pitch` | 1.0 | 0.5 to 2.0, keeping the tempo |

Gains are from -24 to 24 dB, and values out of range raise `ValueError`.

## Queues

```python
--8<-- "docs/snippets/voice/queue_player.py"
```

`voice.Queue(vc)` plays tracks back to back without gaps, or overlapping by `crossfade` seconds. It takes `volume`, `filters`, `crossfade` and `history`, which default to the connection's settings, and the sink's `buffer` and `stall`.

- `queue.add(source, meta=None, index=None)` queues a source at the end, or at `index` among the upcoming tracks, and returns its `Track`.
- `queue.now`, `queue.upcoming` and `queue.history` are the current track, the next ones, and up to `history` that played.
- `queue.skip()` ends the current track; `queue.back()` plays the last track of the history again, then the current one anew.
- `queue.move(track, index)`, `queue.remove(track)`, `queue.shuffle()` and `queue.clear()` rearrange the upcoming tracks.
- `queue.loop = "track"` repeats the current track, `"queue"` the whole queue, `None` stops repeating; the type is `voice.Loop`. Repeats use `source.again()`.
- `queue.pause()`, `queue.resume()`, `queue.paused`, `queue.volume` and `queue.filters` act on whatever plays.
- `queue.stop()` ends every track; tracks added later play again.
- `await queue.join()` waits until nothing is queued or playing.

A queue on a connection that ended is `closed` and takes no more tracks; make a new one after reconnecting, as the snippet does.

## Soundboard sounds

`await bot.rest.soundboard.send(vc.channel_id, sound_id=...)` plays a soundboard sound in the channel the bot is connected to. Discord plays it to the call itself, next to whatever the bot sends; pass `source_guild_id` for a sound of another guild. `bot.rest.soundboard.list_default()` lists Discord's own sounds and `list(guild_id)` a guild's, and `bot.request_soundboard_sounds(guild_id)` asks the gateway for them.

## The same audio in several guilds

`await voice.broadcast(source, connections)` plays one source on several connections, decoding and encoding it once. Connections that end leave the set; ones that join later are not added. It takes `encoder` and `stall`, `"drop"` by default so that a stalled connection does not hold back the others.

## Encoder

`voice.EncoderSettings` configures the Opus encoder of a connection, through `Defaults(encoder=...)`, `connect(encoder=...)` or `vc.encoder = ...` while connected:

| Field | Default | Meaning |
|---|---|---|
| `bitrate` | `None` | bits per second, 500 to 512 000; `None` follows the channel's bitrate, or 64 000 |
| `application` | `"audio"` | `"audio"` for music, `"voip"` for speech, `"lowdelay"` for the least latency |
| `fec` | `True` | forward error correction, which hides lost packets |
| `packet_loss` | 0.1 | the expected loss, 0 to 1, that `fec` plans for |
| `complexity` | 10 | 0 to 10; lower uses less CPU |
| `bandwidth` | `"auto"` | the highest audio band: `"narrow"`, `"medium"`, `"wide"`, `"superwide"` or `"full"` |
| `signal_type` | `"auto"` | tunes for `"voice"` or `"music"` |

`vc.speaking_flags` sets the speaking flags the bot announces, `voice.SpeakingFlags.MICROPHONE` by default, `SOUNDSHARE` for shared audio, `PRIORITY` for priority speaker.
