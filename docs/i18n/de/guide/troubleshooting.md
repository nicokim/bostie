---
source: 14575d90a6a022a3c0cbfce22c048b404bcf4957
---

# Fehlerbehebung

![Bostie denkt nach, bis aus dem Fragezeichen eine Glühbirne wird](../assets/mascot/bostie-thinking.webp){ align=right width="180" loading=lazy }

bostie loggt für jeden Fehler, den es erkennen kann, eine Warnung. Diese Seite listet sie nach dem, was du beobachtest, mit der Logzeile, nach der du suchen musst, und der Lösung.

## Ein Befehl meldet „Die Anwendung hat nicht geantwortet“ { #a-command-says-the-application-did-not-respond }

| Logzeile | Ursache | Lösung |
|---|---|---|
| `nothing handles interaction ... (command 'name')` | Discord hat noch einen Befehl, den der laufende Bot nicht definiert, oft nach einer Umbenennung mit `auto_sync=False`, oder der Befehl lebt in einem anderen Prozess | synchronisiere die Befehle ([Befehle registrieren](registration.md)) |
| `nothing handles interaction ... (custom_id '...')` | keine Vorlage von `on_component` oder `on_modal` passt auf die Custom-ID | gleiche die Vorlage mit den IDs ab, die du sendest; baue sie mit `route.id(...)` |
| `... returned without answering` | der Handler ist ohne `respond`, `defer` oder `modal` fertig geworden | antworte auf jedem Pfad, auch bei frühen Returns |
| `... returned after deferring without answering` | er hat aufgeschoben und nie geantwortet, also bleibt „denkt nach …“ stehen, bis das Token abläuft | ruf nach der langsamen Arbeit `respond` auf |
| `interaction ... reached its handler ... s after it was received; something is blocking the event loop` | blockierender Code in einem Handler oder Listener hat die Event-Loop über Discords 3 Sekunden hinaus festgehalten | verlagere die Arbeit nach `asyncio.to_thread` oder senke `auto_defer` |
| `interaction ... was replayed ... s after it was created; its token is dead` | die Interaktion kam beim Nachspielen einer fortgesetzten Sitzung, zu spät für eine Antwort | nichts; der Benutzer kann sie erneut ausführen |

Eine unbehandelte Exception in einem Handler wird samt Traceback geloggt, und der Benutzer sieht „Something went wrong.“: Füge einen [Fehler-Hook](checks.md#error-hooks) hinzu, um etwas Besseres zu antworten.

## Events kommen nicht an { #events-do-not-arrive }

- `... listens to EVENT, which needs the INTENT intent; add it to Bot(intents=...)`: Der Bot hat den Intent nicht angefordert. Füge ihn hinzu, und schalte einen privilegierten Intent zusätzlich im Developer Portal ein.
- `start()` löst `PrivilegedIntentsRequired` aus: Der Bot hat einen privilegierten Intent angefordert, den das Portal nicht erlaubt. Schalte ihn unter **Bot** → **Privileged Gateway Intents** ein oder entferne ihn aus `intents`.
- `message.content` ist auf Servern leer: Der Intent `MESSAGE_CONTENT` fehlt.
- `EVENT has an unexpected shape (...); typed listeners skip it`: Discord hat einen Payload gesendet, der nicht zur Event-Klasse passt, einmal pro Event-Name geloggt. `RawEvent`-Listener bekommen ihn trotzdem; bitte melde die Zeile.
- `old` ist bei Update-Events `None`: Der Cache hatte das Objekt nicht; was er behält, steht unter [Cache](cache.md#what-it-keeps).

## Das Gateway { #the-gateway }

- `shard N paused reading: ... bytes of events are waiting because the event loop is not keeping up`: Etwas blockiert die Event-Loop; siehe [Backpressure](gateway.md#backpressure).
- `close code N (...) means Discord refused what bostie sent; this is likely a bostie bug, please report it`: Die Codes 4001, 4002, 4005 und 4008 bedeuten, dass bostie etwas gesendet hat, das Discord abgelehnt hat.
- `the members of guild N stopped arriving; the cache lacks some`: Eine Mitgliederanfrage für den Cache ist nach 30 Sekunden pro Chunk abgelaufen.
- `start()` löst `InvalidToken` aus: Das Token ist falsch oder wurde zurückgesetzt; kopiere es erneut von der Seite **Bot**.

## REST { #rest }

- `N invalid requests (401, 403, 429) in the last 10 minutes; Discord bans the IP at 10 000`: Der Bot stellt immer wieder Anfragen, die Discord ablehnt, meist wegen fehlender Berechtigungen oder eines toten Webhooks. Finde die fehlschlagenden Aufrufe anhand der Exceptions, die sie auslösen.
- `RateLimited` wird ausgelöst, ohne dass eine Anfrage Discord erreicht hat: Die Route bleibt länger begrenzt als `max_rate_limit_wait`; erhöhe den Wert in [`HTTPSettings`](rest.md#rate-limits-retries-and-timeouts) oder versuch es später erneut.

## Antworten { #answers }

- `ephemeral=... is ignored because the message being edited is ...`: Eine Nachricht kann nach dem Senden nicht mehr zwischen ephemer und öffentlich wechseln.
- `Discord allows 5 follow-ups to an interaction of an app installed only to the user`: Die sechste Folgenachricht einer für den Benutzer installierten App schlägt fehl.
- `the error hook ... left interaction ... unanswered; bostie answered 'Something went wrong.'`: Lass den Hook antworten.

## Sprachkanäle { #voice }

- `nobody hears the bot in guild N: it is server muted` oder `it is suppressed on this stage; use vc.stage('request')`: Ein Moderator hat den Bot stummgeschaltet, oder er ist auf einer Stage im Publikum.
- `a moderator deafened the bot in guild N, so Discord sends it no audio`: Receiver bekommen nichts, bis er nicht mehr taubgeschaltet ist.
- `VoiceConnectError` mit dem Grund `TIMEOUT`: Discord hat nicht innerhalb von `timeout` Sekunden auf den Beitritt geantwortet; der Kanal ist vielleicht voll, oder dem Bot fehlt die Berechtigung „Verbinden“.
- `MissingIntent: voice needs Intents.GUILD_VOICE_STATES`: Sprachverbindungen brauchen diesen Intent, der in `Intents.DEFAULT` enthalten ist.

## Einen Fehler melden { #reporting-a-bug }

`python -m bostie` gibt die Versionen aus, die du angeben solltest, und `bostie.voice.versions()` ergänzt bei Problemen mit Sprachverbindungen libopus und DAVE. Setz den betreffenden Logger vor dem Start des Bots auf `DEBUG`, wie [Logging](running.md#logging) zeigt, und füge die Zeilen rund um das Problem bei, ohne das Token.
