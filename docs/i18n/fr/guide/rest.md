---
source: 18ff3457c37f07dad7f0bcfc3aee4b5f5383fac0
description: "Toute l'API REST Discord pour les bots : pagination, purge de messages, journal des actions, erreurs, limites de débit, téléchargements et requêtes brutes."
---

# API REST

![Bostie sautant pour attraper une balle](../assets/mascot/bostie-jumping.webp){ align=right width="180" loading=lazy }

`bot.rest` a une méthode pour chaque opération de l'API REST de Discord qu'un bot peut appeler, regroupées selon la ressource sur laquelle elles agissent : `bot.rest.messages.create`, `bot.rest.members.add_role`, `bot.rest.guilds.fetch`. Les requêtes passent par le client HTTP Rust de bostie, qui respecte les limites de débit de Discord.

```python
--8<-- "docs/snippets/rest/rest_calls.py"
```

## Conventions { #conventions }

- Les id de l'URL viennent en premier, positionnels uniquement, dans l'ordre de l'URL : `bot.rest.members.add_role(guild_id, user_id, role_id)`.
- Tout le reste est un argument nommé comme dans la documentation de Discord, vérifié par un `TypedDict` propre à chaque opération : une faute de frappe est donc une erreur de type, et une `TypeError` à l'exécution.
- Les opérations que Discord consigne dans le journal des actions acceptent `reason=`, de 1 à 512 caractères, affiché aux modérateurs du serveur.
- Un champ d'image ou de son, comme l'`image` d'un émoji ou l'`icon` d'un serveur, accepte un `File` ; bostie l'envoie sous forme de data URI, comme Discord l'attend. Les champs de permissions acceptent des `Permissions`.
- Un émoji, comme celui qu'ajoute `reactions.create`, accepte un émoji Unicode, un émoji personnalisé sous la forme `<:name:id>`, `<a:name:id>` ou `name:id`, ou un modèle `Emoji` ou `PartialEmoji`.
- Les méthodes nommées `fetch` récupèrent un objet, `list` en parcourt plusieurs, `edit` modifie, `create` crée et `delete` supprime. Les autres verbes gardent le mot de la documentation, comme `members.add_role` ou `guilds.begin_prune`.
- Les méthodes renvoient des modèles, ou `None` quand Discord répond sans contenu. Elles ne modifient pas `bot.cache`, sauf `channels.create_dm`, qui conserve le salon de MP quand `max_dm_channels` le permet ; ce sont les événements de la gateway qui suivent un changement qui mettent le cache à jour.

## Pagination { #paging }

Les méthodes `list` des endpoints que Discord pagine renvoient un itérateur asynchrone, qui récupère la page suivante au fur et à mesure : `async for message in bot.rest.messages.list(channel_id, limit=500):`, comme le fait `/today` ci-dessus. Sans `limit`, il parcourt tous les éléments. `before`, `after` et `around` commencent ailleurs, un seul d'entre eux au plus ; ils acceptent un id ou un `datetime`, que bostie convertit en l'id de ce moment. Les messages arrivent du plus récent au plus ancien, ou du plus ancien au plus récent avec `after`. La méthode `list` d'un endpoint que Discord ne pagine pas est une coroutine qui renvoie un tuple. `audit_log.list` produit des objets `AuditLogPage` entiers, avec les utilisateurs, les webhooks et les autres objets auxquels les entrées font référence.

## Purger des messages { #purging-messages }

`messages.purge(channel_id, limit=100, check=None)` parcourt les `limit` derniers messages, du plus récent au plus ancien, et supprime ceux pour lesquels `check` renvoie vrai, ou tous sans `check` ; `limit=None` parcourt tout le salon, et `before` et `after` commencent ailleurs, comme dans `list`. Les messages de moins de 14 jours partent dans des suppressions groupées de 100 au plus, les plus anciens à raison d'une requête chacun, car Discord ne supprime en masse que les messages récents ; un type que Discord ne permet pas de supprimer, comme le message de départ d'un fil, est ignoré. `bulk=False` supprime un message à la fois, comme dans un MP, où les suppressions groupées n'existent pas. La méthode renvoie les messages supprimés, et `reason` va dans le journal des actions. Les suppressions groupées et les messages des autres demandent la permission Gérer les messages.

```python
--8<-- "docs/snippets/rest/purge.py"
```

## Le journal des actions { #the-audit-log }

`audit_log.list(guild_id)` produit des objets `AuditLogPage`, et la gateway envoie chaque nouvelle entrée sous forme d'événement `AuditLogEntryCreate`. Les `changes` d'une entrée contiennent un `AuditLogChange` par champ modifié, dont `new_value` et `old_value` ont le type que l'objet modifié donne à ce champ : `Permissions` pour les `permissions` d'un rôle, un `datetime` pour le `communication_disabled_until` d'un membre, un tuple de `Overwrite` pour les `permission_overwrites` d'un salon. Les exceptions de Discord suivent sa documentation : `$add` et `$remove` sur les rôles d'un membre contiennent des objets `AuditLogRole`, l'`avatar_hash` d'un webhook et le `channel_id` d'une invitation prennent le type de `avatar` et d'un id, et un changement de permission de commande a pour clé l'id du rôle, de l'utilisateur ou du salon et contient une `CommandPermission`. Une clé que bostie ne connaît pas, ou une valeur d'une forme que le champ n'accepte pas, garde sa valeur JSON, et une valeur que Discord omet vaut `None`.

```python
--8<-- "docs/snippets/rest/audit_log.py"
```

## Erreurs { #errors }

Une requête à laquelle Discord répond par un statut d'erreur lève une `HTTPException` : `BadRequest` pour 400, `Forbidden` pour 403, `NotFound` pour 404, `ServerError` pour 5xx. L'exception porte le `status`, le `code` de Discord sous forme de `JSONErrorCode`, son `message`, les `errors` par champ et le `body` brut ; son texte liste chaque erreur de champ sur sa propre ligne. `UnexpectedResponse` signifie que Discord a accepté la requête mais que sa réponse n'a pas pu être décodée ; son message indique si le changement a pris effet, et `error.raw` contient la réponse.

## Limites de débit, nouvelles tentatives et délais d'expiration { #rate-limits-retries-and-timeouts }

Le client Rust suit le bucket de limite de débit de chaque route ainsi que la limite globale de Discord, et retient une requête jusqu'à ce que son bucket ait de la place. `Bot(http_settings=HTTPSettings(...))` change ce comportement :

| Champ de `HTTPSettings` | Par défaut | Signification |
|---|---|---|
| `global_rate_limit` | 50 | requêtes par seconde, toutes routes confondues ; augmentez-la si Discord a accordé au bot une limite plus élevée |
| `max_rate_limit_wait` | 30.0 | l'attente la plus longue pour une limite de débit ; une requête qui attendrait plus longtemps lève `RateLimited` sans être envoyée |
| `retries` | 2 | le nombre de nouvelles tentatives pour une requête échouée, avec un backoff exponentiel qui attend au plus `max_rate_limit_wait` |
| `connect_timeout` | 10.0 | secondes pour ouvrir une connexion |
| `read_timeout` | 60.0 | secondes d'attente d'une réponse |
| `rate_limiter` | `True` | `False` envoie immédiatement et attend seulement la fin des 429 qui reviennent, pour un usage derrière un proxy de limite de débit |
| `proxy` | `None` | une URL de proxy HTTP ou HTTPS pour chaque requête, audio `voice.Url` et téléchargements du CDN compris, identifiants inclus comme dans `http://user:password@host:3128` |

Un 429 est retenté après le délai que donne Discord, si celui-ci ne dépasse pas `max_rate_limit_wait`. Un 500, 502, 503, 504 ou 524, ou une connexion coupée, est retenté `retries` fois pour toutes les méthodes sauf `POST`, qui a pu prendre effet ; une connexion qui ne s'est jamais ouverte est retentée pour toutes les méthodes.

Discord bannit une IP qui envoie 10 000 requêtes invalides (401, 403, 429) en 10 minutes. bostie les compte et journalise un avertissement à 1000, 5000 et 9000. À 9500, il refuse chaque requête avec `RequestBudgetExhausted` au lieu de l'envoyer, jusqu'à ce que le compte des 11 dernières minutes repasse sous 9000.

## N'importe quel endpoint { #any-endpoint }

`bot.http.request(method, route, **params)` envoie n'importe quelle requête par le même client et renvoie le JSON décodé, ou `None` pour une réponse vide. La route garde ses espaces réservés, que les arguments nommés remplissent : `await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=123)`. Elle accepte `json=`, `files=` pour un envoi multipart, `form=` pour des champs de formulaire nommés, `urlencoded=` pour un corps form-urlencoded, `query=` et `reason=`, et lève les mêmes exceptions. `bot.http.send(...)` accepte les mêmes arguments et renvoie les octets bruts de la réponse, pour un endpoint qui ne répond pas en JSON.

## Télécharger des fichiers { #downloading-files }

Les pièces jointes et les images se trouvent sur le CDN de Discord, pas derrière l'API : un téléchargement n'envoie donc aucun token et ne consomme aucune limite de débit. `bot.http` les récupère par le même client Rust, avec ses délais d'expiration, ses nouvelles tentatives et son `proxy` :

| Méthode | Effet |
|---|---|
| `read(source)` | renvoie les octets |
| `save(source, path)` | les écrit dans le fichier situé à `path` |
| `to_file(source)` | renvoie un `File` à envoyer de nouveau ; depuis une `Attachment`, il garde le nom du fichier, la description, le titre, le spoiler et les détails de message vocal ; depuis une URL, c'est la dernière partie de son chemin qui le nomme |

`source` est une `Attachment`, ou une URL telle que celles que construisent les utilitaires [`bostie.cdn`](formatting.md). Un fichier absent lève `NotFound`.

```python
--8<-- "docs/snippets/rest/downloads.py"
```

## Sans bot { #without-a-bot }

Un script ou un backend web qui n'a besoin que de REST utilise un `RESTClient` :

```python
--8<-- "examples/rest_script.py"
```

`async with RESTClient(token) as client:` ouvre le client HTTP ; `client.rest` a tous les groupes de `bot.rest`, et `client.http` le client brut. Le token vaut par défaut `BOSTIE_TOKEN`, et le client accepte `api_url`, `http_settings` et `allowed_mentions`, comme le bot. Le [guide OAuth2](oauth2.md) couvre `BearerClient`, qui appelle l'API avec le token d'un utilisateur, et le [guide des webhooks](webhooks.md) les clients qui n'ont besoin d'aucun token.

## Les groupes { #the-groups }

| `bot.rest.` | Méthodes |
|---|---|
| `application` | `edit`, `fetch`, `fetch_activity_instance`, `fetch_bot_application`, `list_role_connection_metadata`, `update_role_connection_metadata` |
| `audit_log` | `list` |
| `auto_moderation` | `create`, `delete`, `edit`, `fetch`, `list` |
| `bans` | `bulk_create`, `create`, `fetch`, `list`, `remove` |
| `channels` | `add_recipient`, `create`, `create_dm`, `create_group_dm`, `delete`, `delete_permission`, `edit`, `edit_permissions`, `edit_positions`, `fetch`, `fetch_member_permissions`, `fetch_role_permissions`, `follow`, `list`, `remove_recipient`, `set_voice_status`, `trigger_typing_indicator` |
| `commands` | `bulk_overwrite`, `bulk_overwrite_guild`, `create`, `create_guild`, `delete`, `delete_guild`, `edit`, `edit_guild`, `fetch`, `fetch_guild`, `fetch_permissions`, `list`, `list_guild`, `list_permissions` |
| `emojis` | `create`, `create_application`, `delete`, `delete_application`, `edit`, `edit_application`, `fetch`, `fetch_application`, `list`, `list_application` |
| `gateway` | `fetch`, `fetch_bot` |
| `guilds` | `begin_prune`, `edit`, `edit_incident_actions`, `edit_welcome_screen`, `edit_widget`, `fetch`, `fetch_preview`, `fetch_prune_count`, `fetch_vanity_url`, `fetch_welcome_screen`, `fetch_widget`, `fetch_widget_image`, `fetch_widget_settings`, `leave`, `list` |
| `identities` | `delete`, `fetch_profile`, `list`, `list_by_external_id`, `update_profile` |
| `integrations` | `delete`, `list` |
| `invites` | `add_target_user`, `bulk_add_target_users`, `bulk_delete_target_users`, `create`, `delete`, `fetch`, `fetch_target_users_job_status`, `list`, `list_guild`, `list_target_users`, `remove_target_user`, `update_target_users` |
| `lobbies` | `add_member`, `bulk_update_members`, `create`, `create_invite_for_user`, `delete`, `edit`, `fetch`, `remove_member`, `update_message_moderation_metadata` |
| `members` | `add`, `add_role`, `edit`, `edit_current`, `fetch`, `fetch_permissions`, `list`, `remove`, `remove_role`, `search` |
| `messages` | `bulk_delete`, `create`, `crosspost`, `delete`, `edit`, `fetch`, `list`, `list_pins`, `pin`, `purge`, `search`, `unpin` |
| `monetization` | `consume_entitlement`, `create_test_entitlement`, `delete_test_entitlement`, `fetch_entitlement`, `fetch_subscription`, `list_entitlements`, `list_skus`, `list_subscriptions` |
| `onboarding` | `edit`, `fetch` |
| `partner_sdk` | `create_provisional_token`, `exchange_child_token`, `exchange_external_token`, `unmerge_external_account`, `unmerge_provisional_account`, `update_dm_moderation_metadata` |
| `polls` | `end`, `list_answer_voters` |
| `reactions` | `create`, `delete`, `delete_all`, `delete_all_for_emoji`, `delete_own`, `list` |
| `roles` | `create`, `delete`, `edit`, `edit_positions`, `fetch`, `fetch_member_counts`, `fetch_permissions`, `list` |
| `scheduled_events` | `create`, `delete`, `edit`, `fetch`, `list`, `list_users` |
| `soundboard` | `create`, `delete`, `edit`, `fetch`, `list`, `list_default`, `send` |
| `stage_instances` | `create`, `delete`, `edit`, `fetch` |
| `stickers` | `create`, `delete`, `edit`, `fetch`, `fetch_guild`, `fetch_pack`, `list`, `list_packs` |
| `templates` | `create`, `delete`, `edit`, `fetch`, `list`, `sync` |
| `threads` | `add_member`, `fetch_member`, `join`, `leave`, `list_active`, `list_joined_private_archived`, `list_members`, `list_private_archived`, `list_public_archived`, `remove_member`, `start`, `start_from_message`, `start_in_forum` |
| `users` | `edit_current`, `fetch`, `fetch_current` |
| `voice` | `edit_current_state`, `edit_state`, `fetch_current_state`, `fetch_state`, `list_guild_regions`, `list_regions` |
| `webhooks` | `create`, `delete`, `delete_message`, `delete_with_token`, `edit`, `edit_message`, `edit_with_token`, `execute`, `execute_github`, `execute_slack`, `fetch`, `fetch_message`, `fetch_with_token`, `list`, `list_guild` |

Les paramètres et le type de retour de chaque méthode figurent dans la [référence de l'API REST](../reference/api/rest.md). Quelques opérations sont volontairement laissées à `bot.http.request` : les endpoints que Discord a dépréciés ou n'a jamais documentés, ainsi que les endpoints de callback d'interaction et de réponse originale, que couvrent les [contextes d'interaction](responses.md).

Certains groupes servent des fonctionnalités de Discord qui vont au-delà des bots sur des serveurs : `monetization` et `application` pour la [monétisation et les Activités](platform.md), `lobbies`, `identities` et `partner_sdk` pour le [Social SDK](platform.md#social-sdk), et `application.update_role_connection_metadata` pour les [rôles liés](oauth2.md#linked-roles).
