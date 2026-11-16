---
source: 0402229d2b242ba10aed929b9dedd0087f57b406
description: "Höre mit typisierten Listenern auf Discords Gateway-Events, warte auf Events oder streame sie, nutze Roh- und eigene Events und sieh, welche Intents nötig sind."
---

# Events

![Bostie begrüßt eine klingelnde Glocke](../assets/mascot/bostie-events.webp){ align=right width="180" loading=lazy }

Discord teilt dem Bot über Gateway-Events mit, was passiert: Eine Nachricht wurde gesendet, ein Mitglied ist beigetreten, eine Rolle hat sich geändert. Ein Listener ist eine asynchrone Funktion, die ein Event entgegennimmt.

```python
--8<-- "docs/snippets/events/listeners.py"
```

`@bot.listen` liest den Type Hint des ersten Parameters und registriert die Funktion für diese Event-Klasse oder für jede Klasse einer Union. `@bot.listen(MessageUpdate)` nennt die Klassen stattdessen ausdrücklich, für eine Funktion ohne Hint oder mit einem weiter gefassten. Die Klassen liegen in `bostie.events`, und die [Event-Liste](../reference/event-list.md) nennt jede mit ihren Intents und Feldern. Ein [Router](routers.md) hat denselben Decorator.

Jeder Listener läuft als eigener Task, sodass ein langsamer Listener weder die anderen noch das nächste Event aufhält. Der Task startet sofort: Der Code eines Listeners bis zu seinem ersten `await` läuft, bevor das nächste Event verarbeitet wird. Die Ausnahme sind die Listener von `Starting` und `Stopped`: `start()` und `close()` warten nacheinander auf sie. `bot.remove_listener(function)` meldet eine Funktion von jedem Event ab, auf das sie hört.

## Nur das, worauf du hörst { #only-what-you-listen-to }

bostie lässt seinen Rust-Kern nur die Dispatch-Namen weiterleiten, die etwas im Bot verwendet: deine Listener, der Cache, das Routing von Befehlen und Komponenten und laufende Wartevorgänge. Jedes andere Event wird verworfen, bevor es Python erreicht; auf nichts zu hören kostet also nichts. Typisierte Events werden einmal pro Dispatch dekodiert, und nur, wenn jemand sie braucht.

## Intents { #intents }

Die meisten Events sendet Discord nur an einen Bot, der ihre Intents angefordert hat. `Bot(intents=...)` ist standardmäßig `Intents.DEFAULT`, jeder Intent außer den drei privilegierten. Registriert sich ein Listener für ein Event, das keiner der Intents des Bots liefert, loggt bostie eine Warnung mit dem Intent, den du hinzufügen musst, und erinnert dich daran, dass ein privilegierter Intent zusätzlich im Developer Portal eingeschaltet werden muss. [Intents](gateway.md#intents) behandelt sie im Detail.

## Alte Werte und der Cache { #old-values-and-the-cache }

Update-Events enthalten `old`, das Objekt, wie es der [Cache](cache.md) vor der Änderung hatte, und Delete-Events enthalten das entfernte Objekt: `RoleUpdate.old`, `ScheduledEventUpdate.old`, `MessageDelete.message`, `MemberRemove.member`. Sie sind `None`, wenn der Cache das Objekt nicht hatte. Nachrichten werden nur mit `Bot(max_messages=...)` gecacht, Mitglieder nur mit `members=True`, Präsenzen nur mit `presences=True`.

## Auf ein Event warten { #waiting-for-an-event }

```python
--8<-- "docs/snippets/events/waiting.py"
```

`await bot.wait_for(Event, check=None)` gibt das nächste Event dieser Klasse zurück, für das `check` wahr zurückgibt. `async with bot.stream(Event, check=None) as events:` reiht jedes passende Event ein, solange der Block läuft, für `async for`; Events, die eintreffen, während der Schleifenrumpf läuft, gehen nicht verloren. Keines von beiden hat ein eigenes Timeout, also begrenze sie mit `asyncio.timeout()`. Eine Exception, die `check` auslöst, wird aus dem Warten heraus ausgelöst. `bot.wait_for_component` und `bot.wait_for_modal` tun dasselbe für [Komponentenverwendungen](components.md#waiting-for-a-click-inside-a-handler) und [abgesendete Modals](modals.md#waiting-for-the-submit-inside-a-handler).

## Roh-Events und Events, die bostie nicht modelliert { #raw-events-and-events-bostie-does-not-model }

```python
--8<-- "docs/snippets/events/custom_events.py"
```

`@bot.listen_raw("NAME", ...)` liefert ein `RawEvent` für jeden angegebenen Dispatch-Namen, ob modelliert oder nicht, und ein Listener mit dem Hint `RawEvent` bekommt jeden Dispatch. Ein `RawEvent` hat den Dispatch-Namen `name`, seine Sequenznummer `seq` und den Payload als `raw` (jedes Mal ein neues `dict`) oder `raw_json` (Bytes). Jedes typisierte Event hat ebenfalls `raw` und `raw_json`.

Sendet Discord einen Payload, der nicht zu seiner Klasse passt, etwa ohne ein Feld, das die Dokumentation als erforderlich bezeichnet, loggt bostie einmal pro Event-Name eine Warnung, und typisierte Listener überspringen dieses Event, während `RawEvent`-Listener es trotzdem bekommen.

## Eigene Events { #events-of-your-own }

Leite von `BotEvent` ab, mit `kw_only=True`, und sende eine Instanz mit `bot.dispatch(event)`: Ihre Listener, Wartevorgänge und Streams bekommen sie wie jedes andere Event. Eine Unterklasse ohne `NAME` wird nach ihrem Modul und ihrer Klasse benannt. Event-Klassen sind eingefrorene `msgspec`-Structs, ihre Felder werden also als Annotationen deklariert.

## Lebenszyklus- und Shard-Events { #lifecycle-and-shard-events }

bostie sendet eigene Events über den Bot: `Starting`, `Started`, `Stopping` und `Stopped` rund um [den Lebenszyklus](running.md#the-lifecycle); `ShardReady`, `ShardDisconnect`, `ShardStop` und `ShardBackpressure` zu jedem [Shard](sharding.md); `ListenerError` und `TaskError` zu Fehlern. Sprachverbindungen senden [Sprach-Events](../voice/index.md#events) über dasselbe `@bot.listen`.

## Wenn ein Listener eine Exception wirft { #when-a-listener-raises }

Eine Exception in einem Listener wird als `ListenerError`-Event gesendet, mit `listener`, `event` und `error`. Ohne `ListenerError`-Listener loggt bostie die Exception samt Traceback; mit einem liegt das Logging bei dir. Eine Exception in einem `ListenerError`-Listener wird nur geloggt.
