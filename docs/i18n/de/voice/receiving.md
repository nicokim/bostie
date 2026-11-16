---
source: 0d8bbdce2b1aa0ef1ee6009ade35eb117f7d7909
description: "Empfange Discord-Audio pro Sprecher als PCM, Opus oder RTP, erkenne Sprache, trenne Äußerungen, leite Anrufe weiter, nimm jeden Benutzer als Ogg oder WAV auf."
---

# Empfangen und aufnehmen

![Bostie hält ein Mikrofon, während Schallwellen ankommen](../assets/mascot/bostie-receiving.webp){ align=right width="180" loading=lazy }

`vc.receive()` öffnet einen Receiver: einen begrenzten asynchronen Iterator über das Audio, das der Bot hört. Jeder Sprecher kommt einzeln an, entschlüsselt, wieder in Reihenfolge gebracht und in Rust dekodiert. An einer Verbindung können mehrere Receiver offen sein, jeder mit eigenem Format und eigenen Filtern.

```python
--8<-- "docs/snippets/voice/listening.py"
```

## Formate { #formats }

`vc.receive(format)` gibt einen `voice.Receiver` für ein `voice.ReceiveFormat` zurück:

| `vc.receive(format)` | Elemente | Für |
|---|---|---|
| `voice.PCM(rate=48000, channels=2, sample="s16le")` | `Frame`s mit dekodierten Samples in diesem Format; die Rate ist eine der `voice.DECODE_RATES`, 8, 12, 16, 24 oder 48 kHz | Zuhören, Spracherkennung, Pegel |
| `voice.Opus()` | `Frame`s, deren `data` ein Opus-Paket ist | Aufnehmen oder Weiterleiten ohne Dekodieren |
| `voice.Packets()` | `Packet`s, entschlüsselt, in Ankunftsreihenfolge, vor dem Jitter-Puffer | deinen eigenen Jitter-Puffer, RTP-Header-Erweiterungen |

Ein `Frame` hat `user_id`, `ssrc`, den RTP-`timestamp`, `offset` und `samples` auf der 48-kHz-Uhr des Receivers, `received_at`, `data`, `lost` (wahr für Audio, das Opus erfunden hat, um ein verlorenes Paket zu kaschieren) und bei PCM `level`, den RMS des Frames in dBFS. Die Uhr startet, wenn der Receiver öffnet, sodass sich Frames verschiedener Benutzer über `offset` ausrichten lassen. Ein `Packet` hat `user_id`, `ssrc`, `sequence`, `timestamp`, `received_at`, die `opus`-Nutzdaten und die RTP-Header-`extensions`.

## Auswählen, wen du hörst { #choosing-whom-to-hear }

- `users=[...]` behält nur diese Benutzer, und `exclude=[...]` lässt einige weg.
- `include_self=True` fügt das eigene Audio des Bots hinzu.
- `mix=True` (PCM und Opus) liefert einen Stream, in dem alle gemischt sind, mit `user_id` auf `None`.
- `markers=True` setzt `SpeechStart`-, `SpeechStop`- und `UserLeave`-Events zwischen die Frames, jeweils mit ihrem `offset` auf derselben Uhr, sodass ein Verbraucher weiß, wo Sprache beginnt und endet, ohne selbst zu messen.

## Einstellungen { #settings }

`vc.receive` nimmt diese Schlüsselwortargumente; `Packets()` hat keinen Jitter-Puffer und nimmt daher kein `jitter`.

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `buffer` | `Defaults.receive_buffer`, 2.0 | Sekunden an Elementen, die der Receiver für einen langsamen Verbraucher hält, 0,02 bis 60 |
| `overflow` | `Defaults.overflow`, `"drop_oldest"` | wenn der Puffer voll ist: die ältesten Elemente verwerfen, oder `"drop_newest"` |
| `jitter` | `Defaults.jitter`, 0.1 | Sekunden, die auf verspätete Pakete gewartet wird, bevor es weitergeht, 0 bis 1; nur PCM und Opus |
| `stall` | `Defaults.receive_stall`, `"wait"` | wenn sich die Verbindung neu verbindet: `"wait"` hält den Receiver offen, `"end"` beendet ihn |

## Lesen { #reading }

`async for item in rx` wartet auf Elemente und hört auf, wenn der Receiver endet. `async with rx:` schließt ihn, wenn der Block endet.

- `rx.get_nowait()` gibt das nächste Element zurück, oder löst `voice.Empty` aus, wenn nichts eingereiht ist, und `voice.ReceiverClosed`, wenn der Receiver beendet ist.
- `rx.queued` ist, wie lange das älteste eingereihte Element schon wartet: der Rückstand des Verbrauchers.
- `rx.clear()` verwirft alles Eingereihte und macht live weiter.
- `await rx.mark()` gibt `True` zurück, sobald das vor dem Aufruf empfangene Audio eingereiht oder verworfen ist, oder `False` nach 1,2 Sekunden plus dem doppelten Jitter; das Abbrechen eines Wartens lässt die Marke für die anderen bestehen.
- `rx.stats()` gibt ein `ReceiverStats` zurück: `delivered` gelieferte Elemente, durch Überlauf `dropped` verworfene und unterwegs `lost` verlorene Pakete.
- `rx.close()` beendet den Empfang. `rx.closed_reason`, ein `voice.ClosedReason`, sagt, warum ein Receiver endete: `CLOSED`, `DISCONNECTED`, `MOVED` (der Bot hat den Kanal gewechselt) oder `RECONNECTING` (mit `stall="end"`).

Ein Moderator kann den Bot taubschalten, und Discord sendet ihm dann kein Audio; bostie loggt eine Warnung, wenn ein Receiver öffnet, während der Bot taubgeschaltet ist, und wenn der Bot taubgeschaltet wird, während Receiver offen sind.

## Weiterleiten { #relaying }

Ein Receiver und ein Sink lassen sich direkt verbinden, um Audio von einem Anruf in einen anderen zu tragen:

```python
--8<-- "docs/snippets/voice/relay.py"
```

Ein gemischter Frame hat weder `user_id` noch `ssrc` oder `timestamp`, und ein Mix kann keine Marker tragen. Ein verlorener Opus-Frame hat leere `data`, weil es kein Paket zum Weiterreichen gibt; überspring ihn, bevor du in einen Opus-Sink schreibst, wie es `relay_speaker` tut. Beide Enden verwenden standardmäßig 48-kHz-Stereo-16-Bit-PCM, sodass ein PCM-Receiver direkt in einen Standard-Sink schreibt.

## Sprache { #speech }

Die Spracherkennung läuft auf jeder Verbindung in Rust, solange `speech_activity` eingeschaltet ist: Ein Benutzer beginnt mit seinem ersten Audio zu sprechen und hört nach `speech_hangover` Sekunden Stille auf, standardmäßig 0,2. `vc.speaking` enthält, wer gerade spricht, die Events `SpeechStart` und `SpeechStop` melden es an Listener, und Receiver, die mit `markers=True` geöffnet wurden, tragen es in ihrem Stream. Ist `speech_activity` aus und kein Receiver offen, entschlüsselt bostie das eingehende Audio nicht einmal.

`voice.utterances(rx, min_silence=0.8, max_length=30.0)` gruppiert die Frames eines PCM-Receivers zu `Utterance`s: die Sprache eines Benutzers von Anfang bis Ende, mit `data` im Format des Receivers, ihrem `offset`, `duration` und den `lost` verlorenen Sekunden. Eine Äußerung endet nach `min_silence` Sekunden ohne Sprache oder bei `max_length` Sekunden. Es braucht einen PCM-Receiver ohne `mix`, der mit `markers=True` geöffnet wurde, oder einen `level` in dBFS, der dann statt der Marker entscheidet, was als Sprache zählt.

## Aufnehmen { #recording }

```python
--8<-- "docs/snippets/voice/call_recording.py"
```

`voice.Recorder(vc, path)` nimmt jeden Benutzer in einer eigenen Datei auf, oder mit `mix=True` den ganzen Anruf in einer. Der Pfad ist eine Vorlage mit `{guild_id}` und `{user_id}`, erforderlich für eine Datei pro Benutzer und bei `mix=True` abgelehnt, und seine Dateiendung bestimmt den Container:

- `.ogg` oder `.opus` schreibt Ogg Opus. Pro Benutzer werden die empfangenen Opus-Pakete unverändert geschrieben, ohne Dekodierung.
- `.wav` schreibt 16-Bit-PCM im `format` des Recorders, standardmäßig 48 kHz Stereo; seine Rate ist eine der `voice.DECODE_RATES`.

Mit `align=True`, dem Standard, füllt Stille die Lücken, sodass jede Datei beginnt, als die Aufnahme begann, und die Dateien zueinander passen. `users`, `exclude` und `include_self` wählen wie bei einem Receiver, wer aufgenommen wird, und `buffer` (30 Sekunden) und `jitter` (0,3 Sekunden) sind größer als die Standardwerte eines Receivers, weil eine Festplatte ins Stocken geraten kann. Die Aufnahme endet von selbst nach `max_duration` Sekunden, sobald ihre Dateien `max_size` Bytes enthalten, oder wenn die Verbindung endet.

`await recorder.stop()` beendet sie, sobald das bereits gehörte Audio geschrieben ist, und gibt die Dateien als Dict von Benutzer-ID auf `Path` zurück, mit `None` als Schlüssel einer gemischten Datei. `await recorder.wait()` wartet, bis sie von selbst endet. `recorder.files` listet die bisher geschriebenen Dateien und `recorder.stats()` die Zähler des Receivers, wobei `dropped` bedeutet, dass die Festplatte länger als `buffer` zurückgefallen ist.

Sag den Leuten im Anruf, dass der Bot sie aufnimmt, wie es das Snippet tut.
