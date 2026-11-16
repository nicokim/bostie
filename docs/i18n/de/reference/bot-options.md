---
source: b62f5277d6f68b2edc4dafd4ea9b8084ddfffea0
description: "Alle Bot-Optionen von bostie mit Standardwerten: Intents, Präsenz, Sharding, Befehls-Sync, automatisches Aufschieben, Cache, HTTP, Sprachkanäle, Beenden."
---

# Bot-Optionen

Jede Option von `Bot(...)`, alle nur als Schlüsselwortargument. Die Anleitungen erklären jede im Zusammenhang.

## Gateway und Identität { #gateway-and-identity }

| Option | Standard | Bedeutung |
|---|---|---|
| `intents` | `Intents.DEFAULT` | die [Intents](../guide/gateway.md#intents), die angefordert werden; der Standard enthält jeden Intent außer den drei privilegierten |
| `status` | `Status.ONLINE` | der [Präsenz](../guide/gateway.md#presence)-Status beim Login |
| `activities` | `()` | Aktivitäten beim Login, gebaut mit `bostie.activity` |
| `afk` | `False` | ob sich der Bot als abwesend meldet |
| `idle_since` | `None` | das `datetime`, seit dem der Bot inaktiv ist |
| `large_threshold` | 50 | 50 bis 250: die Mitgliederzahl, ab der Discord Offline-Mitglieder aus dem Payload eines Servers weglässt |
| `shard_count` | `"auto"` | die Anzahl der [Shards](../guide/sharding.md), oder `"auto"` für Discords Empfehlung |
| `shard_ids` | `"all"` | die Shards, die dieser Prozess betreibt; erfordert ein ausdrückliches `shard_count` |
| `before_identify` | `None` | `async def f(shard_id)`, auf das vor jedem Identify gewartet wird, um Prozesse zu koordinieren |
| `session_file` | `None` | ein Pfad, unter dem `close()` die Sitzungen speichert und der nächste `start()` sie [fortsetzt](../guide/sharding.md#resuming-after-a-restart) |
| `guild_ready_timeout` | 2.0 | Sekunden ohne neuen Server, nach denen ein Shard als [bereit](../guide/cache.md#startup) gilt |

## Befehle und Interaktionen { #commands-and-interactions }

| Option | Standard | Bedeutung |
|---|---|---|
| `auto_sync` | `True` | [die Befehle](../guide/registration.md) beim Start synchronisieren, wenn sie von denen bei Discord abweichen |
| `dev_guilds` | `()` | Server-IDs, auf denen Befehle statt global registriert werden |
| `contexts` | `None` | wo globale Befehle verwendet werden können, sofern ein Befehl oder Router nichts anderes sagt |
| `integration_types` | `None` | welche Installationen globale Befehle mitbringen, sofern ein Befehl oder Router nichts anderes sagt |
| `translate` | `None` | `f(text, locale)`, das eine [Übersetzung](../guide/registration.md#translations) oder `None` zurückgibt |
| `auto_defer` | 2.5 | Sekunden, nach denen eine unbeantwortete Interaktion [aufgeschoben](../guide/responses.md#deferring) wird; `None` schaltet das ab |
| `allowed_mentions` | `AllowedMentions(parse=("users", "roles"))` | welche Erwähnungen standardmäßig [anpingen](../guide/responses.md#allowed-mentions) dürfen |
| `owner_ids` | `None` | die Benutzer, die [`is_owner`](../guide/checks.md#roles-and-owners) durchlässt; `None` liest den Eigentümer oder das Team der App |

## Cache { #cache }

| Option | Standard | Bedeutung |
|---|---|---|
| `cache` | `True` | den [Cache](../guide/cache.md) führen; `False` behält nichts |
| `members` | `False` | jedes Mitglied behalten; braucht `Intents.GUILD_MEMBERS` |
| `chunk_members` | `True` | mit `members=True` die Mitglieder anfordern, die Discord beim Start weglässt |
| `presences` | `False` | Präsenzen behalten; braucht `Intents.GUILD_PRESENCES` |
| `max_messages` | 0 | wie viele der neuesten Nachrichten behalten werden |
| `max_dm_channels` | 0 | wie viele der neuesten DM-Kanäle behalten werden |

## HTTP, Sprachverbindungen und Herunterfahren { #http-voice-and-shutdown }

| Option | Standard | Bedeutung |
|---|---|---|
| `http_settings` | `HTTPSettings()` | [Rate-Limits, Wiederholungen, Timeouts und Proxy](../guide/rest.md#rate-limits-retries-and-timeouts) von REST-Anfragen |
| `api_url` | `None` | die REST-Basis-URL; sonst `BOSTIE_API_URL`, sonst die von Discord |
| `voice` | `voice.Defaults()` | die [Einstellungen für Sprachverbindungen](../voice/index.md#settings), mit denen jede Verbindung startet |
| `shutdown_grace` | 0.0 | Sekunden, die [`close()`](../guide/running.md#the-lifecycle) laufenden Handlern gibt, bevor es sie abbricht |

`run(token=None, loop_factory=None)` und `start(token=None)` nehmen das Token, standardmäßig `BOSTIE_TOKEN`.
