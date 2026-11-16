---
source: b30ad56d0f8b63739574acff05fa92b323c4d3d9
description: "Découpez un bot Discord en routeurs et modules, avec des valeurs par défaut communes, et incluez, excluez ou rechargez à chaud des modules pendant qu'il tourne."
---

# Routeurs et modules

Un `Router` contient des commandes, des écouteurs, des routes, des tâches, des vérifications, des hooks et des hooks d'erreur, comme le bot, sans en être un. Les routeurs découpent un bot en modules, donnent des réglages communs à un groupe de commandes, et peuvent être remplacés pendant que le bot tourne. Le bot est lui-même un routeur : tout ce qui figure sur cette page fonctionne donc aussi sur lui.

```python
--8<-- "docs/snippets/routers/moderation_router.py"
```

Un routeur a tous les décorateurs du bot : `command`, `group`, `user_command`, `message_command`, `entry_point`, `listen`, `listen_raw`, `on_component`, `on_modal`, `every`, `check`, `before_invoke`, `after_invoke` et `on_error`. `bot.include(router)` ajoute tout cela au bot, ainsi que tout ce qui sera enregistré plus tard sur le routeur. Dans ses gestionnaires, `router.bot` est le bot dans lequel il a été inclus ; avant cela, il lève `StateError`.

Les routeurs s'imbriquent : `router.include(other)` fonctionne de la même façon, et inclure le routeur extérieur dans le bot apporte les deux.

## Valeurs par défaut { #defaults }

`Router(...)` accepte, sous forme d'arguments nommés, des valeurs par défaut pour les commandes et les routes enregistrées sur lui :

| Valeur par défaut | S'applique à |
|---|---|
| `ephemeral` | les réponses de ses commandes et de ses routes |
| `auto_defer` | ses commandes et ses routes |
| `default_permissions` | ses commandes de premier niveau |
| `nsfw` | ses commandes de premier niveau |
| `contexts`, `integration_types` | ses commandes globales |

Une commande ou une route utilise d'abord son propre réglage, puis les valeurs par défaut de son routeur, puis celles de chaque routeur qui l'inclut, du plus proche au plus éloigné, puis celles du bot. Dans l'exemple, `/ban` est masquée aux membres sans la permission Bannir des membres, utilisable uniquement dans les serveurs, et reçoit une réponse éphémère : seul le modérateur qui l'a lancée la voit et peut appuyer sur le bouton.

Les vérifications, les hooks « avant » et les hooks « après » d'un routeur s'appliquent à ses gestionnaires ; ceux des routeurs qui l'incluent s'exécutent aussi, le plus extérieur en premier. Les hooks d'erreur sont recherchés du routeur le plus proche vers l'extérieur, comme l'explique [Vérifications, hooks et erreurs](checks.md#error-hooks).

## Modules { #modules }

`bot.include("greetings")` importe le module `greetings` et inclut le `Router` qu'il nomme `router`. Un module qui n'en a pas lève `TypeError`.

`bot.exclude("greetings")` retire tout ce que le routeur a ajouté, annule ses tâches et fait oublier à Python le module et ses sous-modules : l'inclure de nouveau réimporte donc le fichier. `bot.reload("greetings")` fait les deux en un seul appel : si le nouvel import ou la nouvelle inclusion échoue, l'ancien routeur reste inclus et l'erreur est levée. Le rechargement change immédiatement les gestionnaires ; appelez ensuite `await bot.sync_commands()` quand les définitions des commandes ont changé, comme le fait l'[exemple modulaire](../examples.md#modular-bot) dans sa commande `/reload`.

`exclude` et `reload` acceptent aussi un objet `Router`, pour les routeurs qui ne sont pas le `router` d'un module.

## Conflits { #conflicts }

Deux commandes de même nom, deux commandes utilisateur ou de message de même nom, deux routes de même template, ou une même fonction enregistrée deux fois comme tâche lèvent une exception à l'ajout de la seconde, y compris quand c'est un routeur qui l'apporte. Un routeur ne peut être inclus qu'une fois, et jamais dans lui-même.

## Regarder à l'intérieur { #looking-inside }

`router.commands`, `router.context_menus`, `router.component_routes`, `router.modal_routes`, `router.tasks` et `router.listeners` contiennent ce qui est enregistré : les commandes par nom et serveurs, comme `("rules", ())` pour une commande globale, les commandes utilisateur et de message par type, nom et serveurs, les routes par modèle, les tâches par fonction et les écouteurs par événement. `router.walk_commands()` produit chaque commande slash et sous-commande qui a un gestionnaire. `router.match_component(custom_id)` et `router.match_modal(custom_id)` renvoient la route qu'atteint un custom id et ses valeurs d'espace réservé, ou `None`, et `router.find_error_handlers(error)` produit les hooks d'erreur qui traiteraient une exception, le plus proche en premier.
