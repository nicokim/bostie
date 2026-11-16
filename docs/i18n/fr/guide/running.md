---
source: e5576256eb18c7692967843a18e500f0b9d34fe8
description: "Lancez un bot bostie : token, démarrage et arrêt, exécution dans un programme asyncio ou une application web, journalisation et plusieurs bots par processus."
---

# Lancer un bot

![Bostie fête l'événement sous les confettis](../assets/mascot/bostie-celebrating.webp){ align=right width="180" loading=lazy }

## `bot.run()` { #botrun }

`bot.run()` constitue tout le programme de la plupart des bots. Il configure la journalisation au niveau INFO sur stderr, sauf si le logger racine a déjà des gestionnaires, démarre une boucle d'événements et fait tourner le bot jusqu'à son arrêt. Ctrl+C et, sur le thread principal, SIGTERM le ferment proprement.

Le token provient de la variable d'environnement `BOSTIE_TOKEN`, ou de `bot.run(token)`. Un préfixe `Bot ` est retiré, et un token absent lève `ValueError`. `run` accepte aussi `loop_factory`, une fonction qui crée la boucle d'événements, transmise à `asyncio.run`.

## Le cycle de vie { #the-lifecycle }

`bot.start()` fait le travail derrière `run()` :

1. Il envoie l'événement `Starting` et attend chacun de ses écouteurs tour à tour. Le REST fonctionne à ce stade, la gateway non ; un écouteur qui lève une exception arrête le démarrage.
2. Il [synchronise les commandes](registration.md), sauf si `auto_sync=False`.
3. Il connecte les shards de la gateway. Quand chaque shard est prêt et que ses serveurs sont arrivés, il envoie `Started` une seule fois, démarre les [tâches de fond](tasks.md) et laisse `wait_until_ready()` rendre la main.
4. Il traite les événements jusqu'à `close()`. Une connexion perdue reprend d'elle-même, en attendant plus longtemps après chaque tentative ratée. Quand Discord ferme la gateway définitivement, `start()` lève `InvalidToken` pour le code de fermeture 4004, `ShardingRequired` pour 4011, `PrivilegedIntentsRequired` pour 4014, et `GatewayClosed`, la base des deux derniers, pour 4010, 4012 et 4013.

```python
--8<-- "docs/snippets/running/lifecycle_events.py"
```

`await bot.close()` arrête le bot dans cet ordre :

1. Si `Started` a été envoyé, il envoie `Stopping` et laisse jusqu'à 5 secondes à ses écouteurs, pendant que la gateway est encore ouverte.
2. Il quitte chaque salon vocal, puis ferme chaque shard, ce qui termine sa session avec le code de fermeture 1000, ou la sauvegarde pour une [reprise](sharding.md#resuming-after-a-restart).
3. Il annule les tâches de fond. Les gestionnaires encore en cours disposent de `shutdown_grace` secondes, 0 par défaut, avant d'être annulés à leur tour.
4. Il ferme le client HTTP et envoie `Stopped`, dont il attend les écouteurs. `bot.http`, `bot.rest` et `bot.gateway` n'existent plus.

`close()` peut être appelé de n'importe où, plusieurs fois, et pendant que `start()` tourne ; le bot peut ensuite être redémarré. `async with bot:` le ferme à la fin du bloc.

## Dans un autre programme { #inside-another-program }

Quand votre programme fait déjà tourner une boucle d'événements, démarrez le bot comme une tâche :

```python
--8<-- "docs/snippets/running/inside_asyncio.py"
```

`await bot.wait_until_ready()` rend la main une fois `Started` envoyé, immédiatement s'il l'a déjà été, et lève `StateError` si le bot s'arrête avant ; attendre la tâche `start()` lève alors la cause. `bot.is_ready` indique la même chose sans attendre. L'[exemple d'application web](../examples.md#web-app) fait tourner un bot pendant la durée de vie (lifespan) d'un serveur web et publie des messages depuis une route web.

Comme une connexion perdue reprend d'elle-même, relancer le bot ne sert que s'il échoue au démarrage. `TransportError` et `ServerError` signifient que Discord n'a pas pu répondre, et une nouvelle tentative plus tard peut réussir. `InvalidToken` et `GatewayClosed` demandent d'abord une correction, comme un nouveau token ou un intent activé dans le Developer Portal.

Un bot qui ne répond aux interactions que par HTTP n'a pas besoin de gateway : voir [Interactions HTTP](http-interactions.md). `await bot.open()` le démarre de cette façon et rend la main : il ouvre le client HTTP, récupère l'application et l'utilisateur du bot, envoie `Starting`, synchronise les commandes, démarre les tâches de fond et renvoie l'application. `ASGIApp` l'appelle, et `close()` y met fin. Un script qui n'appelle que l'API REST utilise un [`RESTClient`](rest.md#without-a-bot).

## Pendant l'exécution { #while-it-runs }

| Attribut | Valeur |
|---|---|
| `bot.user` | le `CurrentUser` du bot, une fois connecté |
| `bot.application_id` | l'id de l'application, une fois démarré |
| `bot.latency` | l'aller-retour moyen du heartbeat des shards, en secondes, ou `None` |
| `bot.is_running` | si `start()` ou `open()` fait tourner le bot, jusqu'à ce que `close()` l'arrête |
| `bot.is_ready` | si `Started` a été envoyé depuis le dernier `start()` |
| `bot.rest`, `bot.http` | l'[API REST](rest.md), une fois démarré |
| `bot.gateway` | les [shards de la gateway](gateway.md#raw-gateway-commands), une fois connecté |
| `bot.cache` | le [cache](cache.md) |
| `bot.voice` | les [connexions vocales](../voice/index.md) |
| `bot.auto_defer` | le délai par défaut de la [réponse différée automatique](responses.md#deferring) ; modifiable à tout moment |

`bot.rest`, `bot.http` et `bot.gateway` lèvent `StateError` avant `start()`.

## Journalisation { #logging }

bostie journalise via le module standard `logging`, sous ces noms :

| Logger | Ce qu'il signale |
|---|---|
| `bostie.bot` | les gestionnaires qui ont échoué ou n'ont pas répondu, les intents dont un écouteur a besoin, les interactions non traitées |
| `bostie.gateway` | les connexions, déconnexions, reprises, la contre-pression (backpressure), les requêtes invalides |
| `bostie.rest` | les limites de débit et les nouvelles tentatives, depuis le client HTTP en Rust |
| `bostie.sync` | ce que chaque synchronisation des commandes a changé |
| `bostie.context` | les réponses qui n'ont pas pu s'appliquer, comme un flag éphémère sur une modification |
| `bostie.events` | les payloads qui ne correspondaient pas à leur événement, une fois par nom d'événement |
| `bostie.voice` | les connexions vocales, DAVE, les sinks, les récepteurs et les lecteurs |
| `bostie.bridge` | le lien entre Python et le cœur Rust |

Le cœur Rust journalise via les mêmes loggers et lit leurs niveaux au démarrage du bot : appelez `logging.getLogger("bostie.gateway").setLevel(logging.DEBUG)` avant `run()` pour voir la gateway en détail.

## Diriger bostie ailleurs { #pointing-bostie-elsewhere }

`Bot(api_url=...)` envoie les requêtes REST vers une autre URL de base, comme un proxy de limite de débit partagé par plusieurs processus ou un [Discord factice](testing.md) dans les tests. Sans cette option, bostie lit `BOSTIE_API_URL` au démarrage du bot, et utilise `https://discord.com/api/v10` si elle n'est pas définie non plus. Derrière un proxy de limite de débit, passez `http_settings=HTTPSettings(rate_limiter=False)`, pour que bostie laisse l'attente au proxy. Le [guide REST](rest.md#rate-limits-retries-and-timeouts) présente les autres réglages HTTP.

## Plusieurs bots dans un processus { #several-bots-in-one-process }

Chaque `Bot` a sa propre connexion au cœur Rust, son propre cache et son propre client HTTP : un seul processus peut donc faire tourner plusieurs bots, chacun démarré et fermé indépendamment, dans une même boucle d'événements.

## Conserver un état { #keeping-state }

Un bot est un objet ordinaire : sous-classez `Bot` pour lui donner des attributs, comme le fait l'[exemple de cycle de vie](../examples.md#lifecycle) avec une connexion à une base de données ouverte dans un écouteur `Starting` et fermée dans un écouteur `Stopped`. Toutes les options de `Bot` sont listées dans la [référence des options de Bot](../reference/bot-options.md).
