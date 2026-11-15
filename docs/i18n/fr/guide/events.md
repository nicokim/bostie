---
source: a5b6afe7ce2baa740ef1fd06599d2895465c3d1c
---

# Événements

![Bostie salue une cloche qui sonne](../assets/mascot/bostie-events.webp){ align=right width="180" loading=lazy }

Discord informe le bot de ce qui se passe par des événements de la gateway : un message a été envoyé, un membre a rejoint le serveur, un rôle a changé. Un écouteur est une fonction asynchrone qui prend un événement.

```python
--8<-- "docs/snippets/events/listeners.py"
```

`@bot.listen` lit l'annotation de type du premier paramètre et enregistre la fonction pour cette classe d'événement, ou pour chaque classe d'une union. `@bot.listen(MessageUpdate)` nomme plutôt les classes, pour une fonction sans annotation ou avec une annotation plus large. Les classes se trouvent dans `bostie.events`, et la [liste des événements](../reference/event-list.md) les nomme toutes, avec leurs intents et leurs champs. Un [routeur](routers.md) dispose du même décorateur.

Chaque écouteur s'exécute dans sa propre tâche : un écouteur lent ne bloque donc ni les autres ni l'événement suivant. La tâche démarre immédiatement : le code d'un écouteur jusqu'à son premier `await` s'exécute avant que l'événement suivant soit traité. Les écouteurs de `Starting` et `Stopped` font exception : `start()` et `close()` les attendent l'un après l'autre. `bot.remove_listener(function)` désenregistre une fonction de tous les événements qu'elle écoute.

## Seulement ce que vous écoutez { #only-what-you-listen-to }

bostie demande à son cœur Rust de ne transmettre que les noms de dispatch que quelque chose dans le bot utilise : vos écouteurs, le cache, le routage des commandes et des composants, et les attentes en cours. Tout autre événement est abandonné avant d'atteindre Python : n'écouter rien ne coûte donc rien. Les événements typés sont décodés une fois par dispatch, et seulement si quelqu'un en a besoin.

## Intents { #intents }

Discord n'envoie la plupart des événements qu'à un bot qui a demandé leurs intents. `Bot(intents=...)` vaut par défaut `Intents.DEFAULT`, c'est-à-dire tous les intents sauf les trois privilégiés. Quand un écouteur s'enregistre pour un événement qu'aucun des intents du bot ne fournit, bostie journalise un avertissement qui nomme l'intent à ajouter, et vous rappelle qu'un intent privilégié doit aussi être activé dans le Developer Portal. La section [Intents](gateway.md#intents) les détaille.

## Anciennes valeurs et cache { #old-values-and-the-cache }

Les événements de mise à jour portent `old`, l'objet tel que le [cache](cache.md) le connaissait avant le changement, et les événements de suppression portent l'objet supprimé : `RoleUpdate.old`, `ScheduledEventUpdate.old`, `MessageDelete.message`, `MemberRemove.member`. Ils valent `None` quand le cache ne contenait pas l'objet. Les messages ne sont mis en cache qu'avec `Bot(max_messages=...)`, les membres qu'avec `members=True`, les présences qu'avec `presences=True`.

## Attendre un événement { #waiting-for-an-event }

```python
--8<-- "docs/snippets/events/waiting.py"
```

`await bot.wait_for(Event, check=None)` renvoie le prochain événement de cette classe pour lequel `check` renvoie vrai. `async with bot.stream(Event, check=None) as events:` met en file d'attente chaque événement correspondant pendant l'exécution du bloc, pour `async for` ; les événements qui arrivent pendant le corps de la boucle ne sont pas perdus. Aucun des deux n'a de délai d'expiration propre : bornez-les avec `asyncio.timeout()`. Une exception levée par `check` est relevée par l'attente. `bot.wait_for_component` et `bot.wait_for_modal` font de même pour les [utilisations de composants](components.md#waiting-for-a-click-inside-a-handler) et les [envois de fenêtres modales](modals.md#waiting-for-the-submit-inside-a-handler).

## Événements bruts et événements que bostie ne modélise pas { #raw-events-and-events-bostie-does-not-model }

```python
--8<-- "docs/snippets/events/custom_events.py"
```

`@bot.listen_raw("NAME", ...)` fournit un `RawEvent` pour chaque nom de dispatch indiqué, modélisé ou non, et un écouteur annoté avec `RawEvent` reçoit tous les dispatchs. Un `RawEvent` a le `name` du dispatch, son numéro de séquence `seq`, et le payload sous forme de `raw` (un nouveau `dict` à chaque fois) ou de `raw_json` (des octets). Chaque événement typé possède aussi `raw` et `raw_json`.

Quand Discord envoie un payload qui ne correspond pas à sa classe, par exemple sans un champ que la documentation dit obligatoire, bostie journalise un avertissement une fois par nom d'événement, et les écouteurs typés ignorent cet événement tandis que les écouteurs `RawEvent` le reçoivent toujours.

## Vos propres événements { #events-of-your-own }

Sous-classez `BotEvent`, avec `kw_only=True`, et envoyez une instance avec `bot.dispatch(event)` : ses écouteurs, attentes et flux la reçoivent comme n'importe quel autre événement. Une sous-classe sans `NAME` est nommée d'après son module et sa classe. Les classes d'événement sont des structs `msgspec` figées : leurs champs se déclarent donc sous forme d'annotations.

## Événements de cycle de vie et de shard { #lifecycle-and-shard-events }

bostie envoie ses propres événements sur le bot : `Starting`, `Started`, `Stopping` et `Stopped` autour du [cycle de vie](running.md#the-lifecycle) ; `ShardReady`, `ShardDisconnect`, `ShardStop` et `ShardBackpressure` pour chaque [shard](sharding.md) ; `ListenerError` et `TaskError` pour les échecs. Les connexions vocales envoient des [événements vocaux](../voice/index.md#events) par le même `@bot.listen`.

## Quand un écouteur lève une exception { #when-a-listener-raises }

Une exception dans un écouteur est envoyée sous forme d'événement `ListenerError` avec le `listener`, l'`event` et l'`error`. Sans écouteur `ListenerError`, bostie journalise l'exception avec sa trace d'appels ; avec un tel écouteur, la journalisation vous revient. Une exception dans un écouteur `ListenerError` est seulement journalisée.
