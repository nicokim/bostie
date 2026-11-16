---
source: 4108c4a61370aa8ef893c1d65371c3d654e43c09
description: "Tritt mit bostie Sprach- und Stage-Kanälen bei: Verbindungseinstellungen, DAVE-Ende-zu-Ende-Verschlüsselung, Events, stockende Medien, externe Sprach-Engines."
---

# Sprachverbindungen

![Bostie hört mit Kopfhörern zu](../assets/mascot/bostie-listen.webp){ align=right width="180" loading=lazy }

Die Sprach-Engine von bostie läuft in seinem Rust-Kern: der Sprach-WebSocket, der UDP-Transport, der 20-ms-Sendetakt, Dekodierung und Resampling, der Mixer, Opus-Kodierung und -Dekodierung, die Transportverschlüsselung und die Ende-zu-Ende-Verschlüsselung DAVE. Python bittet sie, beizutreten, abzuspielen und zu empfangen; das Timing hängt nie von der Event-Loop ab, außer dass Python den Puffer jedes Sinks gefüllt halten muss.

Sprachverbindungen brauchen `Intents.GUILD_VOICE_STATES`, das in `Intents.DEFAULT` enthalten ist. Der Bot hat höchstens eine Sprachverbindung pro Server.

```python
--8<-- "docs/snippets/voice/voice_connection.py"
```

## Verbinden { #connecting }

`await ctx.voice.connect()` tritt dem Sprach- oder Stage-Kanal des Benutzers bei, der den Befehl ausgeführt hat, und kehrt zurück, sobald Audio in beide Richtungen fließen kann. Es findet den Kanal des Benutzers im Cache oder fragt Discord, wenn der Server nicht gecacht ist, und löst `NotInVoice` aus, wenn der Benutzer in keinem Sprachkanal des Servers ist oder die Interaktion aus einer DM kam. `ctx.voice.current` ist die Verbindung des Bots auf diesem Server, oder `None`.

Außerhalb eines Handlers tritt `await bot.voice.connect(channel_id)` einem Kanal per ID bei. Auf einem Server, auf dem der Bot schon verbunden ist, verschiebt es die Verbindung und wendet nur die angegebenen Optionen an. `bot.voice.get(guild_id)` und `bot.voice.connections` geben die offenen Verbindungen zurück, und `await bot.voice.close()` verlässt sie alle, so wie es `bot.close()` tut.

Ein Beitritt, der nicht innerhalb von `timeout` Sekunden bereit ist, löst `VoiceConnectError` mit dem Grund `TIMEOUT` aus; Discord lässt einen Beitritt zu einem vollen Kanal oder zu einem, mit dem sich der Bot nicht verbinden darf, unbeantwortet. Andere Fehler lösen `VoiceConnectError` mit dem Grund, dem Close-Code und Discords Meldung aus.

## Einstellungen { #settings }

`Bot(voice=voice.Defaults(...))` legt fest, womit jede Verbindung startet, und `connect()` nimmt die Verbindungseinstellungen für einen einzelnen Aufruf:

| `voice.Defaults`-Feld | Standard | Bedeutung |
|---|---|---|
| `self_mute`, `self_deaf` | `False` | die eigene Stummschaltung und Taubschaltung des Bots |
| `timeout` | 10.0 | Sekunden, die auf einen Beitritt, ein Verschieben oder eine Änderung gewartet wird |
| `server_change_delay` | 0.0 | Sekunden, die gewartet wird, bevor der Bot Discord zu einem neuen Sprachserver folgt |
| `encoder` | `EncoderSettings()` | der Opus-Encoder; siehe [Audio abspielen](playback.md#encoder) |
| `speaking_flags` | `MICROPHONE` | die `voice.SpeakingFlags`, die der Bot beim Abspielen ankündigt |
| `stage` | `"listen"` | auf einer Stage: `"listen"`, `"request"`, um das Wort zu erbitten, oder `"speak"` |
| `speech_hangover` | 0.2 | Sekunden Stille, bevor ein Sprecher als verstummt gilt, 0,02 bis 10 |
| `speech_activity` | `True` | ob Events für Sprechbeginn und -ende ausgelöst werden |
| `stall_after` | 2.0 | Sekunden, die Medien stocken dürfen, bevor `MediaStalled` es meldet, mindestens 0,02 |
| `sink_buffer`, `stall` | 0.2, `"hold"` | die Standardwerte jedes [Sinks](sinks.md) |
| `receive_buffer`, `jitter`, `overflow`, `receive_stall` | 2.0, 0.1, `"drop_oldest"`, `"wait"` | die Standardwerte jedes [Receivers](receiving.md) |
| `crossfade`, `history` | 0.0, 0 | die Standardwerte jeder [Warteschlange](playback.md#queues) |

`connect()` nimmt `self_mute`, `self_deaf`, `timeout`, `server_change_delay`, `encoder`, `speaking_flags`, `stage`, `speech_hangover`, `speech_activity` und `stall_after` als Schlüsselwortargumente; ein weggelassenes oder `None` behält den Standardwert. `connect_external()` nimmt die ersten drei, `move()` nimmt `timeout`, und `edit()` nimmt `self_mute`, `self_deaf` und `timeout`. Ungültige Werte lösen beim Bauen von `Defaults` `ValueError` aus. Die String-Einstellungen haben die Literal-Typen `voice.StageRole`, `voice.SinkStall`, `voice.ReceiveStall`, `voice.Overflow`, `voice.Sample`, `voice.OpusApplication`, `voice.OpusBandwidth` und `voice.OpusSignal`. `ctx.voice` ist ein `voice.ContextVoice` und `bot.voice` ein `voice.VoiceManager`.

## Die Verbindung { #the-connection }

| `VoiceConnection` | Wert |
|---|---|
| `guild_id`, `channel_id` | wo sie ist |
| `state` | ein `voice.State`: `CONNECTING`, `READY`, `RESUMING`, `RECONNECTING` oder `DISCONNECTED` |
| `members` | die IDs der anderen Benutzer im Anruf |
| `speaking` | die Benutzer, die gerade sprechen, jeweils mit dem `time.monotonic()`, zu dem sie begonnen haben |
| `ssrcs` | jede SSRC im Anruf mit der zugehörigen Benutzer-ID |
| `self_mute`, `self_deaf` | die eigene Stummschaltung und Taubschaltung des Bots, so wie Discord sie angewendet hat |
| `mute`, `deaf`, `suppress` | ob ein Moderator den Bot stumm- oder taubgeschaltet hat, oder ob er auf dieser Stage nicht sprechen darf |
| `latency` | eine `Latency` mit den Umlaufzeiten von WebSocket und UDP in Sekunden, `None`, bis gemessen |
| `dave` | die Ende-zu-Ende-Verschlüsselung des Anrufs; siehe unten |
| `sinks` | die offenen Sinks, älteste zuerst |
| `encoder`, `speaking_flags`, `speech_hangover`, `speech_activity`, `stall_after` | während der Verbindung änderbar |

- `await vc.move(channel_id)` wechselt in einen anderen Sprach- oder Stage-Kanal des Servers und kehrt zurück, sobald dort Medien fließen.
- `await vc.edit(self_mute=..., self_deaf=...)` ändert die eigene Stumm- oder Taubschaltung des Bots, lässt die nicht angegebene, wie sie ist, und kehrt zurück, sobald Discord sie angewendet hat.
- `await vc.stage("request")` erbittet auf einer Stage das Wort, `"speak"` spricht (mit der nötigen Berechtigung), `"listen"` geht zurück ins Publikum.
- `await vc.wait_until(voice.State.READY)` wartet auf einen Zustand und löst `VoiceNotConnected` aus, wenn die Verbindung vorher endet.
- `vc.reconnect()` trennt den Sprach-WebSocket und setzt über einen neuen fort.
- `vc.stop()` schließt jeden Sink, sodass nichts mehr abgespielt wird.
- `await vc.disconnect()` verlässt den Kanal und kehrt zurück, sobald die Verbindung beendet ist, höchstens 2 Sekunden später.

Aufrufe an einer beendeten Verbindung lösen `VoiceNotConnected` aus.

Die Verbindung repariert sich selbst. Ein abgebrochener WebSocket wird fortgesetzt, eine verlorene Sitzung neu verbunden, und wenn ein Moderator den Bot verschiebt, folgt er in den neuen Kanal und sendet ein `Moved`-Event. Trennt ein Moderator ihn oder löscht den Kanal, endet sie mit dem Grund `REMOVED`. bostie warnt, wenn der Bot abspielt, während er vom Server stummgeschaltet oder auf einer Stage unterdrückt ist, und wenn Receiver warten, während er taubgeschaltet ist.

## Ende-zu-Ende-Verschlüsselung { #end-to-end-encryption }

bostie spricht DAVE, Discords Ende-zu-Ende-Verschlüsselung für Sprache, ohne dass du etwas einrichten musst: Es tritt der Verschlüsselungsgruppe des Anrufs bei, verschlüsselt, was es sendet, und entschlüsselt, was es empfängt. `vc.dave` meldet das als `voice.Dave`:

- `protocol_version` ist das verwendete DAVE-Protokoll, 0 für keines;
- `epoch` zählt die Änderungen der Gruppenmitglieder;
- `privacy_code` ist der Code, den Discord-Clients für den Anruf anzeigen;
- `await vc.dave.verification_code(user_id)` gibt den Code zurück, den du mit einem anderen Benutzer über einen anderen Kanal vergleichst, um zu prüfen, dass niemand dazwischensitzt.

Ein `DaveTransition`-Event meldet jede Änderung. Ein Kanal, der DAVE von einem Client verlangt, der es nicht anbietet, schließt mit dem Grund `DAVE_REQUIRED`, und ein fehlgeschlagener DAVE-Schritt löst `DaveError` aus oder beendet die Verbindung mit `DAVE_FAILED`.

## Events { #events }

Sprachverbindungen senden ihre Events über `@bot.listen`. Jedes ist ein `voice.VoiceEvent` mit der `guild_id`. Ein `StateChange` nach `DISCONNECTED` enthält einen `voice.DisconnectReason`: `REQUESTED` (`disconnect()` lief), `REMOVED` (ein Moderator hat den Bot getrennt oder den Kanal gelöscht), `CALL_TERMINATED`, `RATE_LIMITED`, `TIMEOUT`, `SESSION_INVALID`, `AUTHENTICATION_FAILED`, `DAVE_REQUIRED`, `DAVE_FAILED`, `PROTOCOL_ERROR` oder `CLOSED` (der Bot wurde geschlossen).

| Event | Felder | Gesendet, wenn |
|---|---|---|
| `StateChange` | `old`, `new`, `reason`, `code`, `detail` | die Verbindung ihren Zustand geändert hat |
| `Moved` | `old_channel_id`, `new_channel_id`, `by_us` | der Bot verschoben wurde, durch `move()` oder einen Moderator |
| `DaveTransition` | `protocol_version`, `epoch`, `transition_id` | sich die Ende-zu-Ende-Verschlüsselung geändert hat |
| `MediaStalled`, `MediaResumed` | `direction` (`voice.Direction.SEND` oder `RECEIVE`), `duration` | Senden oder Empfangen aufgehört oder wieder begonnen hat |
| `UserJoin`, `UserLeave` | `user_id` | ein Benutzer dem Anruf beigetreten ist oder ihn verlassen hat |
| `SpeakingUpdate` | `user_id`, `ssrc`, `flags` | ein Benutzer seine SSRC und seine Speaking-Flags angekündigt hat |
| `SpeechStart`, `SpeechStop` | `user_id`, `ssrc`, `at`, und beim Stopp `duration` | ein Benutzer angefangen oder aufgehört hat zu sprechen |
| `RtcpPacket` | `packet_type`, `count`, `ssrc`, `user_id`, `reports`, `data` | der Sprachserver ein RTCP-Paket gesendet hat; jeder `ReceptionReport` in `reports` hat `fraction_lost`, `lost`, `highest_sequence`, `jitter`, `last_sender_report` und `delay` eines Streams |

Sie liegen in `bostie.voice`. `SpeechStart` und `SpeechStop` werden nur ausgelöst, solange `speech_activity` eingeschaltet ist.

## Stockende Medien { #stalled-media }

```python
--8<-- "docs/snippets/voice/media_stalls.py"
```

`MediaStalled` mit `direction` `SEND` wird ausgelöst, wenn Audio in einem Sink wartet, aber `stall_after` Sekunden lang nicht hinauskommt, etwa während einer Neuverbindung oder eines DAVE-Übergangs. Mit `RECEIVE` erreichen Pakete einen offenen Receiver, aber so lange lässt sich keines öffnen oder abspielen. `MediaResumed` folgt, sobald wieder Medien fließen, wobei `duration` ab dem Beginn des Stockens zählt. `stall_after` beträgt 2 Sekunden, sofern `voice.Defaults`, `connect(stall_after=...)` oder `vc.stall_after` nichts anderes sagen.

## Einen leeren Kanal verlassen { #leaving-an-empty-channel }

Der Bot bleibt in einem Kanal, bis er die Verbindung trennt. Um zu gehen, wenn der letzte Benutzer geht:

```python
--8<-- "docs/snippets/voice/leave_when_alone.py"
```

## Fehler { #errors }

Fehler bei Sprachverbindungen sind `VoiceError`s: `VoiceConnectError` (ein Beitritt endete, bevor er bereit war; `reason`, `code`, `detail`), `NotInVoice`, `VoiceNotConnected`, `AlreadyConnected`, `VoiceQueueFull` (zu viele Befehle warten auf die Verbindung), `DaveError` und für Audio `SourceError`, `SinkClosed`, `BufferFull`, `Empty` und `ReceiverClosed`, die die nächsten Seiten erklären.

## Externe Sprach-Engines { #external-voice-engines }

```python
--8<-- "docs/snippets/voice/external_engine.py"
```

`await bot.voice.connect_external(guild_id, channel_id)` tritt einem Kanal für eine Sprach-Engine bei, die woanders läuft, und gibt ein `ExternalVoice` mit dem `state` (`VoiceStateUpdate`) und dem `server` (`VoiceServerUpdate`) zurück, mit denen sich die Engine verbindet. bostie sendet auf diesem Server dann kein Audio. `async with external.updates() as updates:` liefert die späteren Sprachserver des Servers und die eigenen Sprachzustände des Bots, und `await external.disconnect()` verlässt den Kanal. Es löst `AlreadyConnected` auf einem Server aus, auf dem bostie seine eigene Verbindung betreibt, und nimmt `self_mute`, `self_deaf` und `timeout`.

## Versionen { #versions }

`voice.versions()` gibt die Versionen von bostie, libopus, der DAVE-Bibliothek und des höchsten DAVE-Protokolls zurück, die in den Build einkompiliert sind.
