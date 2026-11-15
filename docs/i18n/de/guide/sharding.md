---
source: c45c92d8551bab86affc3711ec78970f48e886d8
---

# Sharding und Fortsetzen

![Drei Bosties rennen hintereinander](../assets/mascot/bostie-running.webp){ align=right width="220" loading=lazy }

Discord verteilt die Server eines Bots auf Gateway-Verbindungen, die Shards heißen: Server `g` gehört zu Shard `(g >> 22) % shard_count`. Ein Bot auf mehr als 2500 Servern muss sharden; Discord schließt eine Verbindung ohne Sharding mit Code 4011, und `start()` löst `ShardingRequired` aus.

## Shards in einem Prozess { #shards-in-one-process }

`Bot(shard_count="auto")`, der Standard, fragt Discord, wie viele Shards verwendet werden sollen, und betreibt sie alle. Mehr ist nicht nötig: bostie startet die Shards innerhalb von Discords Identify-Limits, und jeder Shard sendet seine Events an dieselben Listener, denselben Cache und dieselben Handler. `event.shard_id` sagt, über welchen Shard ein Event kam.

## Shards über mehrere Prozesse { #shards-across-processes }

```python
--8<-- "docs/snippets/sharding/cluster.py"
```

`shard_count=16` legt die Gesamtzahl fest, und `shard_ids` wählt die Shards, die dieser Prozess betreibt; `shard_ids` erfordert ein ausdrückliches `shard_count`, und seine IDs müssen verschieden sein und in `range(shard_count)` liegen.

Discord erlaubt pro Bot nur eine begrenzte Zahl gleichzeitiger Identifies. bostie hält dieses Limit innerhalb eines Prozesses ein; über Prozesse hinweg koordiniert `before_identify` sie. bostie wartet vor jedem Identify auf `before_identify(shard_id)`, sodass es auf einen freien Platz in einem Lock warten kann, den sich deine Prozesse teilen. Im Beispiel steht `identify_lock` für diesen gemeinsamen Lock, der für die 5 Sekunden gehalten wird, die Discord zwischen Identifies eines Buckets verlangt. Wirft der Hook eine Exception, loggt bostie den Fehler und führt das Identify trotzdem aus.

## Shard-Events { #shard-events }

| Event | Felder | Gesendet, wenn |
|---|---|---|
| `ShardReady` | `shard_id` | die neue Sitzung eines Shards bereit ist und seine Server angekommen sind |
| `ShardDisconnect` | `shard_id`, `code`, `reason`, `resuming` | eine Verbindung abgebrochen ist; mit `resuming` setzt der Shard seine Sitzung fort und spielt die verpassten Events nach, sonst beginnt er eine neue Sitzung |
| `ShardStop` | `shard_id`, `code`, `reason` | ein Shard endgültig gestoppt hat, weil `close()` lief oder Discord ihn beendet hat |
| `ShardBackpressure` | `shard_id`, `queued_bytes` | ein Shard das Lesen pausiert hat, weil die Event-Loop zurückfällt |

Neuverbindungen und Resumes brauchen nichts von dir. `bot.latency` ist die mittlere Heartbeat-Umlaufzeit über die Shards, und `bot.gateway.shard(shard_id).latency` die eines einzelnen Shards.

## Nach einem Neustart fortsetzen { #resuming-after-a-restart }

Ein Neustart beginnt normalerweise auf jedem Shard eine neue Sitzung, und die Events der Minuten dazwischen gehen verloren. Mit `Bot(session_file="sessions.json")` hält `close()` die Sitzungen offen und schreibt sie in die Datei. Der nächste `start()` liest die Datei, löscht sie und setzt jede gespeicherte Sitzung fort, die zum aktuellen `shard_count` und zu den Shard-IDs passt, sodass Discord die Events nachspielt, die passiert sind, während der Bot nicht lief. Eine Datei, die sich nicht lesen lässt, wird geloggt und ignoriert, und Shards ohne gespeicherte Sitzung führen ein Identify aus.

`bot.run()` beendet sich bei Strg+C und SIGTERM, sodass ein Neustart durch einen Prozessmanager oder ein Deployment die Sitzungen speichert. Ein Absturz tut das nicht, und der nächste Start führt ein Identify aus. Eine Interaktion, die mehr als 5 Sekunden nach ihrer Erstellung nachgespielt wird, wird mit einer Warnung übersprungen, weil sich ihr Token nicht mehr rechtzeitig beantworten lässt.
