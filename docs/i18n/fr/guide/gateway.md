---
source: 3337dc9b9c1508c500eac3639bbfb9fc948f4a27
---

# Intents, présence et gateway

La gateway est le WebSocket par lequel Discord envoie les événements et le bot envoie sa présence et ses requêtes. Le cœur Rust de bostie la maintient connectée : heartbeats, reprises de session, reconnexions, compression et limite de débit d'envoi. Cette page présente ce que le bot lui demande.

## Intents { #intents }

Discord n'envoie un événement qu'à un bot qui a demandé son intent. `Bot(intents=...)` prend un flag `Intents`, `Intents.DEFAULT` s'il est omis : tous les intents sauf les trois privilégiés.

| Intent privilégié | Nécessaire pour |
|---|---|
| `GUILD_MEMBERS` | les événements d'arrivée, de mise à jour et de départ des membres, tous les membres dans le cache, la demande de tous les membres |
| `GUILD_PRESENCES` | les mises à jour de présence et les présences dans le cache |
| `MESSAGE_CONTENT` | le contenu, les embeds, les pièces jointes et les composants des messages dans les serveurs, sauf les messages qui mentionnent le bot ; les messages privés n'ont pas besoin d'intent pour cela |

Un intent privilégié doit aussi être activé, sur la page **Bot** du Developer Portal. Sinon, Discord ferme la connexion avec le code 4014 et `start()` lève `PrivilegedIntentsRequired`, dont le message indique où l'activer. Combinez les intents avec `|` : `Intents.DEFAULT | Intents.MESSAGE_CONTENT`. bostie avertit quand un écouteur a besoin d'un intent qui manque au bot, et lève `MissingIntent` quand une option ou une requête en a besoin.

La voix nécessite `GUILD_VOICE_STATES`, que `DEFAULT` inclut.

```python
--8<-- "docs/snippets/gateway/intent_presets.py"
```

Trois préréglages côtoient les intents individuels : `Intents.DEFAULT`, aussi nommé `Intents.UNPRIVILEGED`, contient tous les intents sauf les privilégiés, `Intents.PRIVILEGED` les trois privilégiés, et `Intents.ALL` les deux. `& ~` retire des intents d'un préréglage, comme ci-dessus.

## Présence { #presence }

```python
--8<-- "docs/snippets/gateway/presence_and_requests.py"
```

`Bot(status=..., activities=[...], afk=False, idle_since=None)` définit ce que le bot affiche à sa connexion. `await bot.update_presence(...)` le modifie sur chaque shard, ainsi que sur les shards qui se connectent plus tard ; une option omise garde sa valeur.

`status` est un `Status` : `ONLINE`, `IDLE`, `DND` ou `INVISIBLE`. `bostie.activity` construit les activités :

| Constructeur | Affiche |
|---|---|
| `activity.playing(name)` | Joue à *name* |
| `activity.listening(name)` | Écoute *name* |
| `activity.watching(name)` | Regarde *name* |
| `activity.competing(name)` | Participe à *name* |
| `activity.streaming(name, url)` | Streame *name*, pour les services de streaming que Discord prend en charge |
| `activity.custom(text)` | un statut personnalisé avec *text* |

Chacun, sauf `custom`, accepte `state=`, une deuxième ligne de texte, et renvoie un `activity.ActivityDict`.

## Requêtes { #requests }

Certaines données n'arrivent que par la gateway, en réponse à une requête :

- `await bot.request_members(guild_id, query="ab", limit=10)` renvoie les membres dont le nom d'utilisateur ou le pseudo commence par `query`, au plus `limit` (de 0 à 100). `user_ids=[...]` demande plutôt jusqu'à 100 membres par id. Sans l'un ni l'autre, la requête demande tous les membres, ce qui nécessite `GUILD_MEMBERS`. `presences=True` ajoute leurs présences et nécessite `GUILD_PRESENCES`. La réponse est un `RequestedMembers` avec `members`, `presences` et `not_found`, les ids que Discord n'a pas trouvés. Discord applique une limite de débit à cette requête : une requête refusée lève `MemberRequestRateLimited` avec `retry_after`.
- `await bot.request_soundboard_sounds(guild_id)` renvoie les sons du soundboard du serveur.
- `await bot.request_channel_info(guild_id)` renvoie le statut vocal et l'heure de début vocal des salons du serveur ; `fields=["status"]` en demande moins.

Chacune attend jusqu'à 30 secondes chaque partie de la réponse, `timeout=` pour les membres, et lève `TimeoutError` passé ce délai. Une requête vers un shard qui démarre une nouvelle session lève `StateError` ; réessayez une fois qu'il est prêt.

## Commandes brutes de la gateway { #raw-gateway-commands }

`bot.gateway.send(op, d)` envoie n'importe quelle commande de la gateway. L'opcode 3 (présence) va à chaque shard ; un payload avec `guild_id` va au shard de ce serveur, un payload avec `guild_ids` à chaque shard avec sa part des ids, et tout le reste au shard 0. Les réponses de Discord arrivent sous forme d'événements, et uniquement pour les noms d'événement que quelque chose écoute : associez donc une commande brute à un écouteur `listen_raw` ou à un `wait_for`.

`bot.gateway` fournit aussi `shard_ids`, les shards que ce processus fait tourner ; `shard_for(guild_id)`, le shard auquel Discord envoie les événements d'un serveur ; `shard(shard_id)`, avec ses propres `send`, `reconnect` et `latency` ; `reconnect()`, qui coupe chaque connexion et reprend les sessions sur de nouvelles ; et `latency`, l'aller-retour moyen du heartbeat. Une commande pour un shard que ce processus ne fait pas tourner lève `ShardNotRunning`, et une commande en file d'attente derrière trop d'autres lève `GatewayQueueFull`.

## Contre-pression { #backpressure }

Le cœur Rust lit les événements aussi vite que Discord les envoie et les transmet à Python. Quand la boucle d'événements prend du retard, un shard arrête de lire jusqu'à ce que Python rattrape son retard, journalise un avertissement et envoie `ShardBackpressure` avec le nombre d'octets en attente. Un long travail bloquant dans un gestionnaire ou un écouteur en est la cause ; déplacez ce travail dans un thread avec `asyncio.to_thread`.
