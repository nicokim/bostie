---
source: 3337dc9b9c1508c500eac3639bbfb9fc948f4a27
---

# Intents, Präsenz und das Gateway

Das Gateway ist der WebSocket, über den Discord Events sendet und der Bot seine Präsenz und Anfragen. Der Rust-Kern von bostie hält es verbunden: Heartbeats, Resumes, Neuverbindungen, Kompression und das Rate-Limit beim Senden. Diese Seite behandelt, was der Bot von ihm verlangt.

## Intents { #intents }

Discord sendet ein Event nur an einen Bot, der seinen Intent angefordert hat. `Bot(intents=...)` nimmt ein `Intents`-Flag, `Intents.DEFAULT`, wenn weggelassen: jeder Intent außer den drei privilegierten.

| Privilegierter Intent | Gebraucht für |
|---|---|
| `GUILD_MEMBERS` | Events zum Beitreten, Aktualisieren und Entfernen von Mitgliedern, alle Mitglieder im Cache, das Anfordern aller Mitglieder |
| `GUILD_PRESENCES` | Präsenz-Updates und Präsenzen im Cache |
| `MESSAGE_CONTENT` | Inhalt, Embeds, Anhänge und Komponenten von Nachrichten auf Servern, außer bei Nachrichten, die den Bot erwähnen; DMs brauchen dafür keinen Intent |

Ein privilegierter Intent muss zusätzlich auf der Seite **Bot** im Developer Portal eingeschaltet werden. Andernfalls schließt Discord die Verbindung mit Code 4014, und `start()` löst `PrivilegedIntentsRequired` aus, dessen Meldung sagt, wo du ihn einschaltest. Intents fügst du mit `|` hinzu: `Intents.DEFAULT | Intents.MESSAGE_CONTENT`. bostie warnt, wenn ein Listener einen Intent braucht, der dem Bot fehlt, und löst `MissingIntent` aus, wenn eine Option oder eine Anfrage einen braucht.

Sprachverbindungen brauchen `GUILD_VOICE_STATES`, das in `DEFAULT` enthalten ist.

```python
--8<-- "docs/snippets/gateway/intent_presets.py"
```

Neben den einzelnen Intents gibt es drei Vorgaben: `Intents.DEFAULT`, auch `Intents.UNPRIVILEGED` genannt, enthält jeden Intent außer den privilegierten, `Intents.PRIVILEGED` die drei privilegierten und `Intents.ALL` beides. `& ~` nimmt Intents aus einer Vorgabe heraus, wie oben.

## Präsenz { #presence }

```python
--8<-- "docs/snippets/gateway/presence_and_requests.py"
```

`Bot(status=..., activities=[...], afk=False, idle_since=None)` legt fest, was der Bot beim Verbinden anzeigt. `await bot.update_presence(...)` ändert es auf jedem Shard und auf Shards, die sich später verbinden; eine weggelassene Option behält ihren Wert.

`status` ist ein `Status`: `ONLINE`, `IDLE`, `DND` oder `INVISIBLE`. `bostie.activity` baut die Aktivitäten:

| Builder | Zeigt |
|---|---|
| `activity.playing(name)` | Spielt *name* |
| `activity.listening(name)` | Hört *name* |
| `activity.watching(name)` | Schaut *name* |
| `activity.competing(name)` | Tritt an in *name* |
| `activity.streaming(name, url)` | Streamt *name*, für die Streaming-Dienste, die Discord unterstützt |
| `activity.custom(text)` | einen benutzerdefinierten Status mit *text* |

Jeder außer `custom` nimmt `state=`, eine zweite Textzeile, und gibt ein `activity.ActivityDict` zurück.

## Anfragen { #requests }

Manche Daten gibt es nur über das Gateway, als Antwort auf eine Anfrage:

- `await bot.request_members(guild_id, query="ab", limit=10)` gibt die Mitglieder zurück, deren Benutzername oder Nickname mit `query` beginnt, höchstens `limit` (0 bis 100). `user_ids=[...]` fragt stattdessen nach bis zu 100 Mitgliedern per ID. Ohne beides fragt es nach jedem Mitglied, was `GUILD_MEMBERS` braucht. `presences=True` fügt ihre Präsenzen hinzu und braucht `GUILD_PRESENCES`. Die Antwort ist ein `RequestedMembers` mit `members`, `presences` und `not_found`, den IDs, die Discord nicht gefunden hat. Discord begrenzt die Rate dieser Anfrage: Eine abgelehnte löst `MemberRequestRateLimited` mit `retry_after` aus.
- `await bot.request_soundboard_sounds(guild_id)` gibt die Soundboard-Sounds des Servers zurück.
- `await bot.request_channel_info(guild_id)` gibt den Sprachstatus und die Startzeit der Sprachsitzung der Kanäle des Servers zurück; `fields=["status"]` fragt nach weniger.

Jede wartet bis zu 30 Sekunden auf jeden Teil der Antwort, bei Mitgliedern `timeout=`, und löst danach `TimeoutError` aus. Eine Anfrage an einen Shard, der gerade eine neue Sitzung beginnt, löst `StateError` aus; versuch es erneut, sobald er bereit ist.

## Rohe Gateway-Befehle { #raw-gateway-commands }

`bot.gateway.send(op, d)` sendet einen beliebigen Gateway-Befehl. Opcode 3 (Präsenz) geht an jeden Shard; ein Payload mit `guild_id` geht an den Shard dieses Servers, einer mit `guild_ids` an jeden Shard mit seinem Anteil der IDs, und alles andere an Shard 0. Discords Antworten kommen als Events an, und nur für Event-Namen, auf die etwas hört; kombiniere einen rohen Befehl also mit einem `listen_raw`-Listener oder einem `wait_for`.

`bot.gateway` bietet außerdem `shard_ids`, die Shards, die dieser Prozess betreibt; `shard_for(guild_id)`, den Shard, an den Discord die Events eines Servers sendet; `shard(shard_id)`, mit eigenem `send`, `reconnect` und `latency`; `reconnect()`, das jede Verbindung trennt und die Sitzungen über neue fortsetzt; und `latency`, die mittlere Heartbeat-Umlaufzeit. Ein Befehl für einen Shard, den dieser Prozess nicht betreibt, löst `ShardNotRunning` aus, und einer, der hinter zu vielen anderen in der Warteschlange steht, `GatewayQueueFull`.

## Backpressure { #backpressure }

Der Rust-Kern liest Events so schnell, wie Discord sie sendet, und reicht sie an Python weiter. Fällt die Event-Loop zurück, hört ein Shard auf zu lesen, bis Python aufgeholt hat, loggt eine Warnung und sendet `ShardBackpressure` mit den wartenden Bytes. Ursache ist lange blockierende Arbeit in einem Handler oder Listener; verlagere diese Arbeit mit `asyncio.to_thread` in einen Thread.
