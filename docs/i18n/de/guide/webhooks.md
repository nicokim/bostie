---
source: efdcb485cd6bec9ca515ad0f0311945137caa315
description: "Sende, bearbeite und lösche Nachrichten per Discord-Webhook-URL aus asynchronem oder synchronem Python ohne Bot; mit Bot erstellst und verwaltest du Webhooks."
---

# Webhooks

Ein Webhook postet Nachrichten in einen Kanal, unter eigenem Namen und eigenem Avatar. Seine URL enthält eine ID und ein Token, und das Token ist alles, was es zum Posten braucht; ein Deploy-Skript oder ein Monitoring-Job kann also ohne Bot Nachrichten senden.

```python
--8<-- "docs/snippets/webhooks/deploy_notice.py"
```

`WebhookClient(url)` liest die ID und das Token aus einer Webhook-URL und löst bei allem anderen `ValueError` aus. Verwende ihn innerhalb von `async with`, das seinen HTTP-Client öffnet und schließt. `SyncWebhookClient(url)` hat dieselben Methoden für Code ohne Event-Loop; jeder Aufruf betreibt seine eigene.

| Methode | Bewirkt |
|---|---|
| `execute(content, **parts)` | postet eine Nachricht und gibt sie zurück, sobald Discord sie gespeichert hat |
| `edit_message(message_id, **parts)` | bearbeitet eine Nachricht, die der Webhook gesendet hat; ein weggelassener Teil bleibt, `None` leert ihn |
| `fetch_message(message_id)`, `delete_message(message_id)` | eine holen oder löschen |
| `fetch()`, `edit(name=..., avatar=...)`, `delete()` | der Webhook selbst |
| `execute_slack(payload)`, `execute_github(payload)` | einen Payload in den Formaten posten, die Discords Slack- und GitHub-kompatible Endpunkte annehmen |

`execute` nimmt die [Nachrichtenteile](messages.md) und außerdem:

- `username` und `avatar_url`, die für diese Nachricht statt der eigenen des Webhooks angezeigt werden;
- `thread_id`, um in einem Thread des Kanals zu posten; die Nachrichtenmethoden nehmen es ebenfalls;
- `thread_name`, um in einem Forum- oder Medienkanal einen Thread zu beginnen, mit `applied_tags`;
- `with_components=True`, nötig für Komponenten an einem Webhook, der keiner App gehört.

Die Clients nehmen `api_url`, `http_settings` und `allowed_mentions` wie der Bot; standardmäßig kann ein Webhook Benutzer und Rollen anpingen, aber nicht `@everyone`.

Antwortet Discord mit 404 Unknown Webhook, wurde der Webhook gelöscht. bostie beantwortet dann jede spätere Anfrage dafür mit demselben 404, ohne sie zu senden, sodass ein toter Webhook nicht auf das [Limit für ungültige Anfragen](rest.md#rate-limits-retries-and-timeouts) angerechnet wird.

## Webhooks mit einem Bot verwalten { #managing-webhooks-with-a-bot }

`bot.rest.webhooks` erstellt und listet die Webhooks eines Kanals oder Servers, was die Berechtigung „Webhooks verwalten“ braucht, und hat auch die Token-Methoden:

- `create(channel_id, name=..., avatar=File(...))` gibt einen `Webhook` zurück, dessen `url` du an einen `WebhookClient` übergeben kannst.
- `list(channel_id)`, `list_guild(guild_id)`, `fetch(webhook_id)`, `edit(webhook_id, ...)` und `delete(webhook_id)` arbeiten mit dem Token des Bots.
- `execute(webhook_id, token, ...)` und die übrigen `_with_token`- und Nachrichtenmethoden sind das, was `WebhookClient` aufruft.

Das Event `WebhooksUpdate` meldet, dass sich die Webhooks eines Kanals geändert haben. Eine Nachricht, die ein Webhook gesendet hat, kommt als `WebhookMessage` an, deren `author` ein `PartialUser` ist und deren `webhook_id` gesetzt ist.
