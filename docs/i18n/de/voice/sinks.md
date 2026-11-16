---
source: e89423f86355edd642290a66c8608f48553e0ff6
description: "Schreibe rohes PCM- oder Opus-Audio über einen Sink in einen Discord-Sprachanruf: Formate, Gegendruck, Marker, Überblendungen, Puffer und Underrun-Zähler."
---

# Sinks und rohes Audio

Ein Sink ist ein begrenzter Puffer, in den Python Audio schreibt und aus dem der Rust-Mixer liest, jeweils 20 ms auf einmal. Jeder Track und jede Warteschlange spielt über einen. Öffne einen eigenen für Audio, das du erzeugst oder von anderswo bekommst: ein Synthesizer, eine Text-to-Speech-Engine, ein Stream aus einem anderen Programm.

```python
--8<-- "docs/snippets/voice/tone_sink.py"
```

## Formate { #formats }

`vc.sink(format)` gibt einen `voice.Sink` für das zurück, was du schreiben wirst, ein `voice.Format`:

- `voice.PCM(rate=48000, channels=2, sample="s16le")`: verschränkte Little-Endian-Samples, `"s16le"` (16-Bit-Ganzzahlen) oder `"f32le"` (32-Bit-Gleitkommazahlen), Mono oder Stereo, bei 8000 bis 192 000 Hz. bostie rechnet in Rust auf 48 kHz um; eine Rate muss sich in Blöcken von höchstens 960 Samples auf 48 kHz abbilden lassen, was jede gängige Rate tut.
- `voice.Opus()`: ein Opus-Paket pro Schreibvorgang. Pakete von 20 ms bei 48 kHz Stereo werden so gesendet, wie sie geschrieben wurden, solange der Sink allein, mit Lautstärke 1 und ohne Filter spielt; alles andere wird dekodiert und gemischt.

`voice.OpusEncoder(pcm_format, encoder=EncoderSettings())` kodiert PCM im Python-Prozess in solche Pakete, für Audio, das du einmal kodieren und an viele Sinks senden willst: `encode(data)` gibt jedes bis dahin vollständige Paket zurück, und `flush()` füllt den Rest mit Stille auf. Seine Bitrate beträgt standardmäßig 64 kbit/s, da es keinem Kanal folgen kann.

## Schreiben { #writing }

- `await sink.write(data)` reiht Audio ein und wartet, solange der Sink bereits `buffer` Sekunden enthält. Dieses Warten ist der Gegendruck, der deinen Erzeuger im Takt der Echtzeit hält.
- `sink.write_nowait(data)` reiht den ganzen Chunk auf einmal ein oder löst `BufferFull` aus.
- `async with sink:` wartet am Ende des Blocks, bis alles Geschriebene abgespielt ist, und schließt dann den Sink; eine Exception im Block verwirft stattdessen, was eingereiht ist.
- `await sink.drain()` wartet, bis alles bisher Geschriebene abgespielt ist; `False`, wenn ein Teil davon verworfen wurde.
- `sink.clear()` verwirft, was eingereiht ist; `clear(until=marker)` nur den Teil vor einem Marker, `clear(since=marker)` nur den Teil danach.
- `sink.close()` verwirft, was eingereiht ist, und schließt. Schreiben in einen geschlossenen Sink löst `SinkClosed` aus.

`sink.mark()` gibt einen `Marker` hinter dem bisher geschriebenen Audio zurück. `await marker` gibt `True` zurück, sobald alles Audio davor abgespielt ist, oder `False`, wenn vorher ein Teil verworfen wurde; `marker.done` sagt, ob das schon feststeht. Über Marker erfährt ein Player, wann ein Track wirklich zu Ende ist, nicht nur, wann er geschrieben wurde.

## Einstellungen { #settings }

`vc.sink` nimmt diese Schlüsselwortargumente; `vc.play` und `voice.Queue` nehmen ebenfalls `buffer` und `stall`.

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `buffer` | `Defaults.sink_buffer`, 0.2 | Sekunden, die der Sink hält, bevor `write` wartet, 0,02 bis 60 |
| `stall` | `Defaults.stall`, `"hold"` | wenn sich Audio nicht senden lässt, während einer Neuverbindung oder eines DAVE-Übergangs: `"hold"` behält es, um es danach abzuspielen, `"drop"` verwirft es in Echtzeit, für Live-Audio, das nicht zurückfallen darf |
| `volume` | 1.0 | multipliziert die Samples; `sink.volume` ändert es während der Wiedergabe |
| `filters` | `Filters()` | die [Effekte](playback.md#volume-and-filters); `sink.filters` ändert sie während der Wiedergabe |
| `after` | `None` | ein anderer Sink dieser Verbindung; dieser bleibt stumm, bis jener schließt oder ausgespielt hat, was vor seinem letzten `drain()` geschrieben wurde |
| `crossfade` | 0.0 | mit `after` die Sekunden, um die früher begonnen und übergeblendet wird; der Sink puffert dann entsprechend mehr |

Mit `after` und `crossfade` spielt eine Warteschlange lückenlos: Der Sink des nächsten Tracks füllt sich, während der aktuelle spielt, und übernimmt auf das Sample genau.

## Zustand und Zähler { #state-and-counters }

`sink.queued` sind die Sekunden wartenden Audios, `sink.passthrough` sagt, ob das letzte Paket unverändert hinausging, und `sink.closed`, ob er geschlossen ist. `sink.stats()` gibt ein `SinkStats` zurück:

| Feld | Wert |
|---|---|
| `sent` | gesendete Sekunden Audio |
| `position` | der Abspielkopf in Sekunden, so wie Marker zählen |
| `underruns` | wie oft der Sink beim Abspielen leerlief, außerhalb eines `drain()`: Der Erzeuger ist zurückgefallen |
| `held_ticks` | Takte, in denen der Sink zurückgehalten wurde, weil sich Audio nicht senden ließ |
| `dropped` | Sekunden, die durch einen `"drop"`-Stillstand verworfen wurden |
| `passthrough_packets` | Pakete, die unverändert gesendet wurden, ohne Dekodierung |

Auf die Underruns solltest du achten: Ein Erzeuger, der nicht `buffer` Sekunden Vorsprung halten kann, ist zu langsam, oder die Event-Loop ist blockiert. `vc.sinks` listet die offenen Sinks einer Verbindung.
