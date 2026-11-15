---
source: e4d65a29086e1f3e4e82a64b8aa6c42d29627ef0
---

# Webhooks

Un webhook publie des messages dans un salon, sous un nom et un avatar qui lui sont propres. Son URL contient un id et un token, et le token suffit pour publier : un script de déploiement ou une tâche de supervision peut donc envoyer des messages sans bot.

```python
--8<-- "docs/snippets/webhooks/deploy_notice.py"
```

`WebhookClient(url)` extrait l'id et le token d'une URL de webhook, et lève `ValueError` pour toute autre chose. Utilisez-le dans un `async with`, qui ouvre et ferme son client HTTP. `SyncWebhookClient(url)` a les mêmes méthodes pour du code sans boucle d'événements ; chaque appel exécute la sienne.

| Méthode | Effet |
|---|---|
| `execute(content, **parts)` | publie un message et le renvoie une fois que Discord l'a enregistré |
| `edit_message(message_id, **parts)` | modifie un message que le webhook a envoyé ; une partie omise reste, `None` l'efface |
| `fetch_message(message_id)`, `delete_message(message_id)` | récupère ou supprime un message |
| `fetch()`, `edit(name=..., avatar=...)`, `delete()` | le webhook lui-même |
| `execute_slack(payload)`, `execute_github(payload)` | publient un payload dans les formats qu'acceptent les endpoints de Discord compatibles Slack et GitHub |

`execute` accepte les [parties d'un message](messages.md), ainsi que :

- `username` et `avatar_url`, affichés à la place de ceux du webhook pour ce message ;
- `thread_id`, pour publier dans un fil du salon ; les méthodes de message l'acceptent aussi ;
- `thread_name`, pour créer un fil dans un salon forum ou multimédia, avec `applied_tags` ;
- `with_components=True`, nécessaire pour les composants d'un webhook qui n'appartient à aucune application.

Les clients acceptent `api_url`, `http_settings` et `allowed_mentions`, comme le bot ; par défaut, un webhook peut mentionner les utilisateurs et les rôles, mais pas `@everyone`.

Quand Discord répond 404 Unknown Webhook, le webhook a été supprimé. bostie répond alors à chaque requête suivante pour ce webhook par le même 404, sans l'envoyer, si bien qu'un webhook mort ne compte pas dans la [limite de requêtes invalides](rest.md#rate-limits-retries-and-timeouts).

## Gérer les webhooks avec un bot { #managing-webhooks-with-a-bot }

`bot.rest.webhooks` crée et liste les webhooks d'un salon ou d'un serveur, ce qui demande la permission Gérer les webhooks, et a aussi les méthodes à token :

- `create(channel_id, name=..., avatar=File(...))` renvoie un `Webhook` dont vous pouvez passer l'`url` à un `WebhookClient`.
- `list(channel_id)`, `list_guild(guild_id)`, `fetch(webhook_id)`, `edit(webhook_id, ...)` et `delete(webhook_id)` fonctionnent avec le token du bot.
- `execute(webhook_id, token, ...)` ainsi que les autres méthodes `_with_token` et de message sont celles qu'appelle `WebhookClient`.

L'événement `WebhooksUpdate` signale que les webhooks d'un salon ont changé. Un message envoyé par un webhook arrive sous forme de `WebhookMessage`, dont l'`author` est un `PartialUser` et dont le `webhook_id` est défini.
