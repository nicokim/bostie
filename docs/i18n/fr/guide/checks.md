---
source: 029b6094446c707896abd078da818c4746bbebec
---

# Vérifications, hooks et erreurs

![Bostie les bras croisés, furieux](../assets/mascot/bostie-angry.webp){ align=right width="180" loading=lazy }

Une vérification décide si un gestionnaire peut s'exécuter. Les hooks exécutent du code avant et après les gestionnaires. Les hooks d'erreur répondent quand quelque chose lève une exception.

## Vérifications { #checks }

```python
--8<-- "docs/snippets/checks/command_checks.py"
```

Une vérification est une fonction qui prend le contexte et renvoie un `bool`, directement ou avec `await`. `False` refuse l'exécution avec `CheckFailure`, et une vérification peut lever sa propre `CheckFailure` : son message est ce que l'utilisateur voit. Les vérifications se placent dans le réglage `checks=[...]` d'une commande, d'un groupe, d'une sous-commande, d'une commande utilisateur ou de message, d'un point d'entrée, ou d'une route de composant ou de fenêtre modale, et `@router.check` en enregistre une pour chaque gestionnaire d'un routeur ; le bot est un routeur.

Les vérifications du bot s'exécutent en premier, puis celles de chaque routeur, du plus externe vers l'intérieur, puis celles des groupes, de haut en bas, puis celles de la commande elle-même. Le premier refus arrête l'exécution. L'autocomplétion n'exécute aucune vérification.

`bostie.checks` fournit les plus courantes :

| Vérification | Refuse avec | Quand |
|---|---|---|
| `has_permissions(permissions)` | `MissingPermissions` | il manque à l'utilisateur l'une des `permissions` dans le salon, ou il est en MP |
| `bot_has_permissions(permissions)` | `BotMissingPermissions` | il manque au bot l'une d'elles dans le salon |
| `Cooldown(rate, seconds, per="user")` | `CooldownActive` | la clé l'a déjà utilisée `rate` fois au cours des `seconds` dernières secondes |
| `MaxConcurrency(number, per="global")` | `MaxConcurrencyReached` | `number` exécutions pour la clé sont encore en cours |
| `has_role(role)` | `MissingRole` | l'utilisateur n'a pas le rôle, donné par id ou par nom, ou il est en MP |
| `has_any_role(*roles)` | `MissingRole` | l'utilisateur n'a aucun des rôles |
| `is_owner` | `NotOwner` | l'utilisateur ne fait pas partie de `bot.owner_ids` |

Un objet de vérification qui possède aussi une méthode `release(ctx)`, un `checks.ReleasingCheck` comme `MaxConcurrency`, est libéré dès que le gestionnaire se termine, quelle que soit la façon dont il se termine.

`per` vaut `"user"`, `"guild"` (l'utilisateur, en MP), `"channel"`, `"global"`, ou une fonction du contexte qui renvoie n'importe quelle clé hachable. Les vérifications de permissions lisent les permissions que Discord envoie avec l'interaction : elles ne nécessitent donc ni cache ni requête. Leurs erreurs portent `missing`, les `Permissions` manquantes, et `CooldownActive` porte `retry_after` en secondes.

## Rôles et propriétaires { #roles-and-owners }

```python
--8<-- "docs/snippets/checks/staff_checks.py"
```

`has_role` et `has_any_role` acceptent des ids ou des noms de rôles. Un id est comparé aux rôles que Discord envoie avec l'interaction : il ne nécessite donc pas de cache. Un nom est recherché parmi les rôles du serveur en cache, si bien qu'un bot sans cache, ou une [application HTTP uniquement](http-interactions.md), désigne les rôles par id. `MissingRole` porte les `roles` demandés, et son message mentionne un rôle donné par id, que Discord affiche sous le nom du rôle.

`is_owner` est une vérification telle quelle, sans parenthèses. `bot.owner_ids` contient les utilisateurs qu'elle laisse passer : les `owner_ids` passés à `Bot(...)`, sinon le propriétaire de l'application, lu depuis l'application au démarrage du bot. Pour une application qui appartient à une équipe, ce sont le propriétaire de l'équipe ainsi que les admins et les développeurs qui ont accepté l'invitation ; les membres en lecture seule sont exclus.

## Ce que voit l'utilisateur { #what-the-user-is-told }

Quand aucun hook d'erreur ne traite une `CheckFailure`, bostie répond avec son message, en éphémère : « You need the Manage Messages permission. », « The bot needs the Manage Messages permission. », « Try again » suivi d'un horodatage relatif comme « in 42 seconds », « This is already running; try again once it is done. », « You need the Moderator role. », « Only the bot's owners can use this. », ou « You cannot use this. » pour un simple `False`.

## Hooks avant et après { #hooks-before-and-after }

```python
--8<-- "docs/snippets/checks/error_hooks.py"
```

`@router.before_invoke` enregistre un `async def hook(ctx)` qui s'exécute une fois les vérifications passées et avant le gestionnaire ; `@router.after_invoke` en enregistre un qui s'exécute après le gestionnaire, y compris s'il a levé une exception. Ils s'exécutent pour chaque commande, route de composant et route de fenêtre modale du routeur : un hook susceptible de voir un composant ne doit donc pas supposer un `Context`. Les hooks « avant » du bot s'exécutent en premier et ses hooks « après » en dernier. Une exception levée par un hook « après » est consignée et ne change pas la réponse.

## Hooks d'erreur { #error-hooks }

Une exception levée par une vérification, un convertisseur, un hook « avant » ou le gestionnaire va à un hook d'erreur. `@x.on_error` enregistre `async def hook(ctx, error)` pour toute `Exception`, et `@x.on_error(NotFound, ValueError)` pour ces types et leurs sous-classes. `x` est une commande, un groupe, une route de composant ou de fenêtre modale, un routeur ou le bot, et chacun peut avoir un hook par type d'exception.

bostie cherche un hook dans cet ordre, et le premier qu'il trouve traite l'erreur :

1. la commande, puis son groupe et le groupe de premier niveau ;
2. le routeur auquel appartient la commande, puis chaque routeur qui l'inclut, jusqu'au bot.

À un même endroit, le hook de la classe la plus proche de l'erreur l'emporte : un hook pour `NotFound` passe avant un hook pour `HTTPException`. Un hook qui relève la même erreur la transmet au hook suivant dans cet ordre, comme le fait `slow_down` ci-dessus. Un hook qui lève une autre erreur la voit consignée.

Le type du contexte est `ErrorContext` : un `Context`, `ComponentContext`, `ModalContext` ou `AutocompleteContext`. Le contexte d'autocomplétion ne peut pas appeler `respond` : testez-le donc comme ci-dessus.

Quand un hook se termine sans répondre, bostie consigne un avertissement et répond « Something went wrong. ». Quand aucun hook ne traite l'erreur, bostie répond à une `CheckFailure` avec son message, et consigne toute autre erreur avec sa trace d'appels avant de répondre « Something went wrong. ». Rien n'est répondu quand le gestionnaire avait déjà répondu, et une autocomplétion reçoit une liste de suggestions vide.

## Erreurs hors des gestionnaires { #errors-outside-handlers }

Les écouteurs et les tâches n'ont pas d'utilisateur à qui répondre. Une exception dans un écouteur est envoyée sous forme d'événement `ListenerError`, et une exception dans une tâche sous forme de `TaskError` ; sans écouteur pour eux, bostie les consigne. Voir [Événements](events.md#when-a-listener-raises) et [Tâches de fond](tasks.md).

## Types d'exceptions { #exception-types }

Tout ce que bostie lève au sujet de Discord, du réseau ou de l'état du bot est une `BostieError`. Un mauvais argument lève plutôt `ValueError` ou `TypeError`. Certaines erreurs sont aussi une exception standard : `StateError` est une `RuntimeError`, `MissingIntent` une `ValueError`, `StartupError` une `OSError` et `CommandDefinitionError` une `TypeError`. Les erreurs liées aux commandes et aux interactions partagent la base `CommandError`, et les erreurs vocales `VoiceError`.

| Exception | Levée quand |
|---|---|
| `HTTPException` | une requête REST s'est terminée avec un statut d'erreur ; `status`, `code` (un `JSONErrorCode`), `message`, `errors` et le `body` brut |
| `BadRequest`, `Forbidden`, `NotFound`, `ServerError` | 400, 403, 404 et 5xx |
| `RateLimited` | 429, ou bostie a retenu une requête qui aurait attendu plus longtemps que `max_rate_limit_wait` ; `retry_after`, `is_global` |
| `InvalidToken` | Discord a rejeté le token, en REST ou sur la gateway |
| `TransportError` | une requête a échoué avant que Discord ne réponde |
| `UnexpectedResponse` | Discord a accepté une requête mais sa réponse ne se décode pas ; `raw` la contient |
| `RequestBudgetExhausted` | trop de requêtes récentes étaient invalides, et bostie en a refusé une pour protéger l'IP |
| `GatewayClosed`, `PrivilegedIntentsRequired`, `ShardingRequired` | Discord a fermé la gateway pour de bon ; les deux dernières pour les codes 4014 et 4011 |
| `ShardNotRunning`, `GatewayQueueFull` | une commande de gateway pour un shard que ce processus n'exécute pas, ou alors que trop de commandes sont en attente |
| `StateError` | un appel que l'état de l'objet ne permet pas, comme `bot.rest` avant `start()` |
| `MissingIntent` | une fonctionnalité nécessite un intent que le bot n'a pas demandé |
| `MemberRequestRateLimited` | Discord a refusé une requête de membres ; `retry_after` |
| `StartupError` | bostie n'a pas pu mettre en place une ressource système dont il a besoin |
| `CommandDefinitionError` | une commande, un groupe ou une option enfreint les règles de Discord ; levée par le décorateur |
| `CommandSyncError` | Discord a rejeté les commandes pendant une synchronisation |
| `OptionConversionError` | une option ou une cible n'a pas pu être convertie vers son annotation de type |
| `InteractionResponded`, `InteractionNotAnswered`, `InteractionExpired` | un appel de réponse que l'état de l'interaction ne permet pas |
| `CheckFailure` et ses sous-classes | une vérification a refusé une exécution |

Les erreurs vocales figurent sur la [page voix](../voice/index.md#errors). La [référence des erreurs](../reference/api/errors.md) liste chaque classe avec ses attributs.
