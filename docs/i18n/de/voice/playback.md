---
source: d85e30b6bb45eff282f70f86375dc49e252a5387
description: "Spiele Dateien, URLs und HLS-Streams in Discord-Sprachkanälen ab: Tracks, Lautstärke, Equalizer, Tempo, lückenlose Warteschlangen, Überblendung, Opus-Encoder."
---

# Audio abspielen

![Bostie wippt im Takt der Musik](../assets/mascot/bostie-playback.webp){ align=right width="180" loading=lazy }

`await vc.play(source)` spielt eine Quelle über eine [Sprachverbindung](index.md) ab und gibt sofort einen `Track` zurück. Mehrere Tracks laufen gleichzeitig und werden gemischt; eine `Queue` spielt sie nacheinander ab.

```python
--8<-- "docs/snippets/voice/tracks.py"
```

## Quellen { #sources }

Eine Quelle ist Audio zum Abspielen. Jede wird nur einmal verwendet: `play` öffnet sie, ein erneutes Öffnen nach ihrem Ende löst `StateError` aus, und `source.again()` gibt eine neue mit demselben Audio zurück, um es zweimal abzuspielen.

| Quelle | Spielt ab | Spulbar |
|---|---|---|
| `voice.File(path)` | eine lokale Datei | ja |
| `voice.Bytes(data, hint=None)` | Dateiinhalt im Speicher; `hint` ist eine Dateiendung wie `"ogg"` | ja |
| `voice.Url(url, headers=None)` | eine direkte HTTP- oder HTTPS-Medien-URL, gestreamt, oder eine HLS-Playlist | wenn der Server Range-Anfragen akzeptiert; HLS nein |
| `voice.FFmpeg(args, executable="ffmpeg")` | die Ausgabe eines ffmpeg-Befehls, für Formate, die bostie nicht dekodiert | nein |
| `voice.Stream(items, format)` | ein asynchrones Iterable aus PCM-Chunks oder Opus-Paketen in `format` | nein |

`File`, `Bytes` und `Url` werden in Rust dekodiert: Ogg (Opus, Vorbis), WebM und Matroska, MP4 und M4A (AAC), MP3, FLAC und WAV. Eine URL muss auf das Medium selbst zeigen, nicht auf eine Webseite, die es einbettet. Bricht die Verbindung zum Server ab, verbindet sich `Url` bis zu dreimal neu, nach 0,5, 1 und 2 Sekunden, und setzt dort fort, wo sie abgebrochen ist, wenn der Server Range-Anfragen akzeptiert. Eine HLS-Master-Playlist spielt ihre Audio-Variante ab, eine Live-Playlist beginnt drei Segmente vor ihrem Ende, und verschlüsselte Playlists und Byte-Range-Segmente werden mit `SourceError` abgelehnt. `FFmpeg` startet das Programm mit deinen Eingabeargumenten und liest 48-kHz-Stereo-16-Bit-PCM aus seiner Ausgabe; es löst `SourceError` aus, wenn das Programm nicht im `PATH` liegt oder mit einem Fehler endet. Eine Quelle, die sich nicht öffnen, dekodieren oder spulen lässt, löst `SourceError` aus oder beendet ihren Track mit dem Grund `ERROR`.

Nach `open()`, das `opened` setzt, hat eine Quelle `format`, `duration` (Sekunden, `None`, wenn unbekannt), `seekable` und `tags`, ein `voice.Tags` mit `title`, `artist` und `album`, die die Datei angibt.

```python
--8<-- "docs/snippets/voice/other_sources.py"
```

Opus-Audio mit 48 kHz Stereo in 20-ms-Paketen, wie die meisten Ogg-Opus- und WebM-Dateien, wird unverändert gesendet, ohne erneutes Dekodieren und Kodieren, solange es allein, mit Lautstärke 1 und ohne Filter läuft. `track.passthrough` sagt, wann das der Fall ist.

## Tracks { #tracks }

`vc.play(source, volume=1.0, filters=Filters(), meta=None)` nimmt die Startlautstärke und die Filter sowie `meta`, einen beliebigen Wert, der beim Track bleibt, etwa wer ihn angefordert hat. Es nimmt außerdem `buffer` und `stall` für den [Sink](sinks.md) des Tracks.

| `Track` | Bewirkt |
|---|---|
| `await track.wait()` | wartet auf das Ende und gibt ein `TrackEnd` zurück; das Warten abzubrechen stoppt den Track nicht |
| `await track.started()` | wartet, bis er zu spielen beginnt; `False`, wenn er vorher endete |
| `track.ended` | das `TrackEnd`, oder `None`, solange er spielt |
| `track.position` | Sekunden der Quelle, die bisher gespielt wurden |
| `track.pause()`, `track.resume()`, `track.paused` | das Einspeisen anhalten und wieder aufnehmen; bereits gepuffertes Audio läuft noch weiter |
| `await track.seek(seconds)` | ab einer Position fortsetzen; `SourceError`, wenn die Quelle nicht spulen kann |
| `track.volume`, `track.filters` | wirken sofort, während er spielt |
| `track.stop()` | beendet ihn |
| `track.source`, `track.meta`, `track.sink` | was er abspielt, dein Wert und der Sink, in den er schreibt |

Ein `TrackEnd` hat `track`, `reason` (`FINISHED`, `STOPPED`, `ERROR` mit `error`, oder `CLEANUP`, wenn die Verbindung endete) und `may_start_next`, wahr nach `FINISHED` oder `ERROR`, das sagt, ob ein eigener Player zu seinem nächsten Track übergehen sollte. `vc.stop()` beendet jeden Track.

## Lautstärke und Filter { #volume-and-filters }

`volume` multipliziert die Samples: 0,5 ist halb so laut, 1,0 lässt sie unverändert. `voice.Filters` legt Effekte fest, die Rust beim Abspielen anwendet:

| `Filters`-Feld | Standard | Bereich |
|---|---|---|
| `equalizer` | `{}` | bis zu 16 Bänder, die eine Mittenfrequenz von 20 bis 20 000 Hz auf eine Verstärkung in dB abbilden; jedes Band ist eine Oktave breit |
| `bass` | 0.0 | Verstärkung in dB unter 100 Hz |
| `speed` | 1.0 | 0,5 bis 2,0, bei gleicher Tonhöhe |
| `pitch` | 1.0 | 0,5 bis 2,0, bei gleichem Tempo |

Verstärkungen reichen von -24 bis 24 dB, und Werte außerhalb des Bereichs lösen `ValueError` aus.

## Warteschlangen { #queues }

```python
--8<-- "docs/snippets/voice/queue_player.py"
```

`voice.Queue(vc)` spielt Tracks lückenlos nacheinander ab, oder um `crossfade` Sekunden überlappend. Sie nimmt `volume`, `filters`, `crossfade` und `history`, deren Standardwerte aus den Einstellungen der Verbindung kommen, sowie `buffer` und `stall` des Sinks.

- `queue.add(source, meta=None, index=None)` reiht eine Quelle am Ende ein, oder an `index` unter den kommenden Tracks, und gibt ihren `Track` zurück.
- `queue.now`, `queue.upcoming` und `queue.history` sind der aktuelle Track, die nächsten und bis zu `history` bereits gespielte.
- `queue.skip()` beendet den aktuellen Track; `queue.back()` spielt den letzten Track aus dem Verlauf erneut ab und danach den aktuellen von vorn.
- `queue.move(track, index)`, `queue.remove(track)`, `queue.shuffle()` und `queue.clear()` ordnen die kommenden Tracks neu.
- `queue.loop = "track"` wiederholt den aktuellen Track, `"queue"` die ganze Warteschlange, `None` beendet die Wiederholung; der Typ ist `voice.Loop`. Wiederholungen verwenden `source.again()`.
- `queue.pause()`, `queue.resume()`, `queue.paused`, `queue.volume` und `queue.filters` wirken auf das, was gerade läuft.
- `queue.stop()` beendet jeden Track; später hinzugefügte Tracks laufen wieder.
- `await queue.join()` wartet, bis nichts mehr eingereiht ist oder läuft.

Eine Warteschlange an einer beendeten Verbindung ist `closed` und nimmt keine Tracks mehr an; erstelle nach dem Neuverbinden eine neue, wie es das Snippet tut.

## Soundboard-Sounds { #soundboard-sounds }

`await bot.rest.soundboard.send(vc.channel_id, sound_id=...)` spielt einen Soundboard-Sound in dem Kanal ab, mit dem der Bot verbunden ist. Discord spielt ihn selbst im Anruf ab, neben dem, was der Bot sendet; übergib `source_guild_id` für einen Sound eines anderen Servers. `bot.rest.soundboard.list_default()` listet Discords eigene Sounds und `list(guild_id)` die eines Servers, und `bot.request_soundboard_sounds(guild_id)` fragt das Gateway danach.

## Dasselbe Audio auf mehreren Servern { #the-same-audio-in-several-guilds }

`await voice.broadcast(source, connections)` spielt eine Quelle über mehrere Verbindungen ab und dekodiert und kodiert sie dabei nur einmal. Verbindungen, die enden, verlassen die Menge; später beitretende werden nicht hinzugefügt. Es nimmt `encoder` und `stall`, standardmäßig `"drop"`, damit eine stockende Verbindung die anderen nicht aufhält.

## Encoder { #encoder }

`voice.EncoderSettings` konfiguriert den Opus-Encoder einer Verbindung, über `Defaults(encoder=...)`, `connect(encoder=...)` oder `vc.encoder = ...` während der Verbindung:

| Feld | Standard | Bedeutung |
|---|---|---|
| `bitrate` | `None` | Bits pro Sekunde, 500 bis 512 000; `None` folgt der Bitrate des Kanals, sonst 64 000 |
| `application` | `"audio"` | `"audio"` für Musik, `"voip"` für Sprache, `"lowdelay"` für die geringste Latenz |
| `fec` | `True` | Vorwärtsfehlerkorrektur, die verlorene Pakete kaschiert |
| `packet_loss` | 0.1 | der erwartete Verlust, 0 bis 1, für den `fec` plant |
| `complexity` | 10 | 0 bis 10; niedriger braucht weniger CPU |
| `bandwidth` | `"auto"` | das höchste Audioband: `"narrow"`, `"medium"`, `"wide"`, `"superwide"` oder `"full"` |
| `signal_type` | `"auto"` | stimmt auf `"voice"` oder `"music"` ab |

`vc.speaking_flags` legt die Speaking-Flags fest, die der Bot ankündigt, standardmäßig `voice.SpeakingFlags.MICROPHONE`, `SOUNDSHARE` für geteiltes Audio, `PRIORITY` für Prioritätssprecher.
