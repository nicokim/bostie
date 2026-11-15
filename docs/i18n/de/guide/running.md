---
source: fecb554b875bf1b19567e85926b3784beca4dba5
---

# Einen Bot betreiben

![Bostie feiert mit Konfetti](../assets/mascot/bostie-celebrating.webp){ align=right width="180" loading=lazy }

## `bot.run()` { #botrun }

Für die meisten Bots ist `bot.run()` das ganze Programm. Es richtet das Logging auf INFO nach stderr ein, sofern der Root-Logger noch keine Handler hat, startet eine Event-Loop und lässt den Bot laufen, bis er stoppt. Strg+C und, im Haupt-Thread, SIGTERM beenden ihn sauber.

Das Token kommt aus der Umgebungsvariable `BOSTIE_TOKEN` oder aus `bot.run(token)`. Ein vorangestelltes `Bot ` wird entfernt, und ein fehlendes Token löst `ValueError` aus. `run` nimmt außerdem `loop_factory`, eine Funktion, die die Event-Loop erzeugt und an `asyncio.run` weitergereicht wird.

## Der Lebenszyklus { #the-lifecycle }

`bot.start()` erledigt die Arbeit hinter `run()`:

1. Es sendet das Event `Starting` und wartet nacheinander auf jeden seiner Listener. REST funktioniert hier, das Gateway nicht; ein Listener, der eine Exception wirft, bricht den Start ab.
2. Es [synchronisiert die Befehle](registration.md), außer bei `auto_sync=False`.
3. Es verbindet die Gateway-Shards. Sind alle Shards bereit und ihre Server angekommen, sendet es einmal `Started`, startet die [Hintergrund-Tasks](tasks.md) und lässt `wait_until_ready()` zurückkehren.
4. Es verarbeitet Events bis zu `close()` oder bis Discord das Gateway endgültig schließt, was eine Exception auslöst, zum Beispiel `InvalidToken` oder `PrivilegedIntentsRequired`.

```python
--8<-- "docs/snippets/running/lifecycle_events.py"
```

`await bot.close()` fährt in dieser Reihenfolge herunter:

1. Wurde `Started` gesendet, sendet es `Stopping` und gibt dessen Listenern bis zu 5 Sekunden, während das Gateway noch offen ist.
2. Es verlässt jeden Sprachkanal und schließt dann jeden Shard, was dessen Sitzung mit dem Close-Code 1000 beendet oder sie für ein [Resume](sharding.md#resuming-after-a-restart) speichert.
3. Es bricht die Hintergrund-Tasks ab. Handler, die noch laufen, bekommen `shutdown_grace` Sekunden, standardmäßig 0, bevor auch sie abgebrochen werden.
4. Es schließt den HTTP-Client und sendet `Stopped`, auf dessen Listener es wartet. `bot.http`, `bot.rest` und `bot.gateway` sind danach weg.

`close()` lässt sich von überall aufrufen, auch mehrmals und während `start()` läuft; der Bot kann danach erneut gestartet werden. `async with bot:` schließt ihn, wenn der Block endet.

## Innerhalb eines anderen Programms { #inside-another-program }

Wenn dein Programm bereits eine Event-Loop betreibt, starte den Bot als Task:

```python
--8<-- "docs/snippets/running/inside_asyncio.py"
```

`await bot.wait_until_ready()` kehrt zurück, sobald `Started` gesendet wurde, sofort, wenn das schon geschehen ist, und löst `StateError` aus, wenn der Bot vorher stoppt; ein `await` auf den `start()`-Task löst dann den Grund aus. `bot.is_ready` sagt dasselbe, ohne zu warten. Das [Web-App-Beispiel](../examples.md#web-app) betreibt einen Bot im Lifespan eines Webservers und postet Nachrichten aus einer Web-Route.

Ein Bot, der nur über HTTP auf Interaktionen antwortet, braucht kein Gateway: siehe [HTTP-Interaktionen](http-interactions.md). `await bot.open()` startet ihn auf diese Weise und kehrt zurück: Es öffnet den HTTP-Client, holt die Anwendung und den Benutzer des Bots, sendet `Starting`, synchronisiert die Befehle, startet die Hintergrund-Tasks und gibt die Anwendung zurück. `ASGIApp` ruft es auf, und `close()` beendet es. Ein Skript, das nur die REST-API aufruft, verwendet einen [`RESTClient`](rest.md#without-a-bot).

## Während er läuft { #while-it-runs }

| Attribut | Wert |
|---|---|
| `bot.user` | der `CurrentUser` des Bots, sobald er verbunden ist |
| `bot.application_id` | die ID der App, sobald er gestartet ist |
| `bot.latency` | die mittlere Heartbeat-Umlaufzeit der Shards in Sekunden, oder `None` |
| `bot.is_running` | ob `start()` oder `open()` den Bot betreibt, bis `close()` ihn beendet |
| `bot.is_ready` | ob seit dem letzten `start()` `Started` gesendet wurde |
| `bot.rest`, `bot.http` | die [REST-API](rest.md), sobald er gestartet ist |
| `bot.gateway` | die [Gateway-Shards](gateway.md#raw-gateway-commands), sobald er verbunden ist |
| `bot.cache` | der [Cache](cache.md) |
| `bot.voice` | die [Sprachverbindungen](../voice/index.md) |
| `bot.auto_defer` | die Standardverzögerung für [automatisches Aufschieben](responses.md#deferring); jederzeit änderbar |

`bot.rest`, `bot.http` und `bot.gateway` lösen vor `start()` `StateError` aus.

## Logging { #logging }

bostie loggt über das Standardmodul `logging` unter diesen Namen:

| Logger | Was er meldet |
|---|---|
| `bostie.bot` | Handler, die fehlgeschlagen sind oder nicht geantwortet haben, Intents, die ein Listener braucht, unverarbeitete Interaktionen |
| `bostie.gateway` | Verbindungen, Verbindungsabbrüche, Resumes, Backpressure, ungültige Anfragen |
| `bostie.rest` | Rate-Limits und Wiederholungen, aus dem Rust-HTTP-Client |
| `bostie.sync` | was jede Befehlssynchronisierung geändert hat |
| `bostie.context` | Antworten, die nicht angewendet werden konnten, etwa ein Ephemeral-Flag bei einer Bearbeitung |
| `bostie.events` | Payloads, die nicht zu ihrem Event passten, einmal pro Event-Name |
| `bostie.voice` | Sprachverbindungen, DAVE, Sinks, Receiver und Player |
| `bostie.bridge` | die Verbindung zwischen Python und dem Rust-Kern |

Der Rust-Kern loggt über dieselben Logger und liest ihre Level beim Start des Bots: Setz `logging.getLogger("bostie.gateway").setLevel(logging.DEBUG)` vor `run()`, um das Gateway im Detail zu sehen.

## bostie auf eine andere Adresse richten { #pointing-bostie-elsewhere }

`Bot(api_url=...)` sendet REST-Anfragen an eine andere Basis-URL, etwa an einen Rate-Limit-Proxy, den sich mehrere Prozesse teilen, oder in Tests an ein [Fake-Discord](testing.md). Fehlt die Angabe, liest bostie beim Start des Bots `BOSTIE_API_URL` und verwendet `https://discord.com/api/v10`, wenn auch das nicht gesetzt ist. Hinter einem Rate-Limit-Proxy übergibst du `http_settings=HTTPSettings(rate_limiter=False)`, damit bostie das Warten dem Proxy überlässt. Die [REST-Anleitung](rest.md#rate-limits-retries-and-timeouts) behandelt die übrigen HTTP-Einstellungen.

## Mehrere Bots in einem Prozess { #several-bots-in-one-process }

Jeder `Bot` hat seine eigene Verbindung zum Rust-Kern, seinen eigenen Cache und seinen eigenen HTTP-Client. Ein Prozess kann also mehrere Bots in einer Event-Loop betreiben, die jeweils für sich gestartet und geschlossen werden.

## Zustand halten { #keeping-state }

Ein Bot ist ein gewöhnliches Objekt: Leite von `Bot` ab, um ihm Attribute zu geben, wie es das [Lebenszyklus-Beispiel](../examples.md#lifecycle) mit einer Datenbankverbindung tut, die in einem `Starting`-Listener geöffnet und in einem `Stopped`-Listener geschlossen wird. Jede `Bot`-Option steht in der [Referenz der Bot-Optionen](../reference/bot-options.md).
