# Sinks and raw audio

A sink is a bounded buffer that Python writes audio into and the Rust mixer reads from, 20 ms at a time. Every track and queue plays through one. Open your own for audio you generate or receive from elsewhere: a synthesizer, a text-to-speech engine, a stream from another program.

```python
--8<-- "docs/snippets/voice/tone_sink.py"
```

## Formats

`vc.sink(format)` returns a `voice.Sink` for what you will write, a `voice.Format`:

- `voice.PCM(rate=48000, channels=2, sample="s16le")`: interleaved little-endian samples, `"s16le"` (16-bit integers) or `"f32le"` (32-bit floats), mono or stereo, at 8000 to 192 000 Hz. bostie resamples to 48 kHz in Rust; a rate must divide into 48 kHz in blocks of at most 960 samples, which every common rate does.
- `voice.Opus()`: one Opus packet per write. Packets of 20 ms at 48 kHz stereo are sent as written while the sink plays alone at volume 1 without filters; anything else is decoded and mixed.

`voice.OpusEncoder(pcm_format, encoder=EncoderSettings())` encodes PCM into those packets in Python's process, for audio you want to encode once and send to many sinks: `encode(data)` returns every complete packet so far, and `flush()` pads the rest with silence. Its bitrate defaults to 64 kbps, since it has no channel to follow.

## Writing

- `await sink.write(data)` queues audio, and waits while the sink already holds `buffer` seconds. That wait is the backpressure that keeps your producer in step with real time.
- `sink.write_nowait(data)` queues the whole chunk at once, or raises `BufferFull`.
- `async with sink:` waits at the end of the block until everything written has played, then closes the sink; an exception in the block discards what is queued instead.
- `await sink.drain()` waits until everything written so far has played; `False` if some of it was dropped.
- `sink.clear()` drops what is queued; `clear(until=marker)` only the part before a marker, `clear(since=marker)` only the part after it.
- `sink.close()` discards what is queued and closes. Writing to a closed sink raises `SinkClosed`.

`sink.mark()` returns a `Marker` after the audio written so far. `await marker` returns `True` once all audio before it played, or `False` if some was dropped first; `marker.done` says whether that is known yet. Markers are how a player knows when a track really ended, not just when it was written.

## Settings

`vc.sink` takes these keyword arguments; `buffer` and `stall` are its `voice.SinkOptions`, which `vc.play` and `voice.Queue` take too.

| Setting | Default | Meaning |
|---|---|---|
| `buffer` | `Defaults.sink_buffer`, 0.2 | seconds the sink holds before `write` waits, 0.02 to 60 |
| `stall` | `Defaults.stall`, `"hold"` | when audio cannot be sent, during a reconnect or a DAVE transition: `"hold"` keeps it to play afterwards, `"drop"` discards it in real time, for live audio that must not fall behind |
| `volume` | 1.0 | multiplies the samples; `sink.volume` changes it while playing |
| `filters` | `Filters()` | the [effects](playback.md#volume-and-filters); `sink.filters` changes them while playing |
| `after` | `None` | another sink of this connection; this one stays silent until that one closes or plays out what was written before its last `drain()` |
| `crossfade` | 0.0 | with `after`, seconds to start early and fade across; the sink then buffers that much more |

`after` and `crossfade` are how a queue plays without gaps: the next track's sink fills while the current one plays, and takes over at the exact sample.

## State and counters

`sink.queued` is the seconds of audio waiting, `sink.passthrough` whether the last packet went out as written, and `sink.closed` whether it is closed. `sink.stats()` returns a `SinkStats`:

| Field | Value |
|---|---|
| `sent` | seconds of audio sent |
| `position` | the playhead in seconds, as markers count it |
| `underruns` | how often the sink ran dry while playing, outside a `drain()`: the producer fell behind |
| `held_ticks` | ticks the sink was held because audio could not be sent |
| `dropped` | seconds discarded by a `"drop"` stall |
| `passthrough_packets` | packets sent as written, without decoding |

Underruns are the number to watch: a producer that cannot keep `buffer` seconds ahead is too slow, or the event loop is blocked. `vc.sinks` lists a connection's open sinks.
