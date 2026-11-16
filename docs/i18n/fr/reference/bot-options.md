---
source: b62f5277d6f68b2edc4dafd4ea9b8084ddfffea0
description: "Chaque option de la classe Bot et sa valeur par défaut : intents, présence, sharding, synchronisation des commandes, réponse différée, cache, HTTP, voix, arrêt."
---

# Options du bot

Toutes les options de `Bot(...)`, toutes nommées uniquement (keyword-only). Les guides expliquent chacune dans son contexte.

## Gateway et identité { #gateway-and-identity }

| Option | Valeur par défaut | Signification |
|---|---|---|
| `intents` | `Intents.DEFAULT` | les [intents](../guide/gateway.md#intents) à demander ; la valeur par défaut contient tous les intents sauf les trois privilégiés |
| `status` | `Status.ONLINE` | le statut de [présence](../guide/gateway.md#presence) à la connexion |
| `activities` | `()` | les activités à la connexion, construites avec `bostie.activity` |
| `afk` | `False` | si le bot se déclare absent |
| `idle_since` | `None` | le `datetime` auquel le bot est devenu inactif |
| `large_threshold` | 50 | de 50 à 250 : le nombre de membres au-delà duquel Discord exclut les membres hors ligne du payload d'un serveur |
| `shard_count` | `"auto"` | le nombre de [shards](../guide/sharding.md), ou `"auto"` pour la recommandation de Discord |
| `shard_ids` | `"all"` | les shards que ce processus fait tourner ; nécessite un `shard_count` explicite |
| `before_identify` | `None` | `async def f(shard_id)` attendu avant chaque identify, pour coordonner les processus |
| `session_file` | `None` | un chemin où `close()` enregistre les sessions et où le `start()` suivant les [reprend](../guide/sharding.md#resuming-after-a-restart) |
| `guild_ready_timeout` | 2.0 | le nombre de secondes sans nouveau serveur après lequel un shard est considéré comme [prêt](../guide/cache.md#startup) |

## Commandes et interactions { #commands-and-interactions }

| Option | Valeur par défaut | Signification |
|---|---|---|
| `auto_sync` | `True` | [synchroniser les commandes](../guide/registration.md) au démarrage quand elles diffèrent de celles de Discord |
| `dev_guilds` | `()` | les id des serveurs où enregistrer les commandes au lieu de les enregistrer globalement |
| `contexts` | `None` | où les commandes globales peuvent être utilisées, sauf si une commande ou un routeur dit autre chose |
| `integration_types` | `None` | quelles installations portent les commandes globales, sauf si une commande ou un routeur dit autre chose |
| `translate` | `None` | `f(text, locale)` qui renvoie une [traduction](../guide/registration.md#translations) ou `None` |
| `auto_defer` | 2.5 | le nombre de secondes après lequel une interaction sans réponse est [différée](../guide/responses.md#deferring) ; `None` le désactive |
| `allowed_mentions` | `AllowedMentions(parse=("users", "roles"))` | quelles mentions peuvent [notifier](../guide/responses.md#allowed-mentions) par défaut |
| `owner_ids` | `None` | les utilisateurs que [`is_owner`](../guide/checks.md#roles-and-owners) laisse passer ; `None` lit le propriétaire ou l'équipe de l'application |

## Cache { #cache }

| Option | Valeur par défaut | Signification |
|---|---|---|
| `cache` | `True` | conserver le [cache](../guide/cache.md) ; `False` ne conserve rien |
| `members` | `False` | conserver tous les membres ; nécessite `Intents.GUILD_MEMBERS` |
| `chunk_members` | `True` | avec `members=True`, demander au démarrage les membres que Discord omet |
| `presences` | `False` | conserver les présences ; nécessite `Intents.GUILD_PRESENCES` |
| `max_messages` | 0 | combien des derniers messages conserver |
| `max_dm_channels` | 0 | combien des derniers salons de messages privés conserver |

## HTTP, voix et arrêt { #http-voice-and-shutdown }

| Option | Valeur par défaut | Signification |
|---|---|---|
| `http_settings` | `HTTPSettings()` | [limites de débit, nouvelles tentatives, délais d'expiration et proxy](../guide/rest.md#rate-limits-retries-and-timeouts) des requêtes REST |
| `api_url` | `None` | l'URL de base de REST ; sinon `BOSTIE_API_URL`, sinon celle de Discord |
| `voice` | `voice.Defaults()` | les [réglages vocaux](../voice/index.md#settings) dont part chaque connexion |
| `shutdown_grace` | 0.0 | le nombre de secondes que [`close()`](../guide/running.md#the-lifecycle) laisse aux gestionnaires en cours avant de les annuler |

`run(token=None, loop_factory=None)` et `start(token=None)` prennent le token, `BOSTIE_TOKEN` par défaut.
