---
source: efdcb485cd6bec9ca515ad0f0311945137caa315
description: "Publica, edita y elimina mensajes con la URL de un webhook de Discord desde código Python async o síncrono sin un bot, y crea y administra webhooks con uno."
---

# Webhooks

Un webhook publica mensajes en un canal con un nombre y un avatar propios. Su URL contiene un id y un token, y el token es todo lo que hace falta para publicar, así que un script de despliegue o un proceso de monitoreo puede enviar mensajes sin un bot.

```python
--8<-- "docs/snippets/webhooks/deploy_notice.py"
```

`WebhookClient(url)` extrae el id y el token de la URL de un webhook, y lanza `ValueError` para cualquier otra cosa. Úsalo dentro de `async with`, que abre y cierra su cliente HTTP. `SyncWebhookClient(url)` tiene los mismos métodos para código sin event loop; cada llamada ejecuta el suyo.

| Método | Qué hace |
|---|---|
| `execute(content, **parts)` | publica un mensaje y lo devuelve una vez que Discord lo guardó |
| `edit_message(message_id, **parts)` | edita un mensaje que envió el webhook; una parte que no se pasa se queda, `None` la borra |
| `fetch_message(message_id)`, `delete_message(message_id)` | obtienen o eliminan uno |
| `fetch()`, `edit(name=..., avatar=...)`, `delete()` | el propio webhook |
| `execute_slack(payload)`, `execute_github(payload)` | publican un payload en los formatos que aceptan los endpoints de Discord compatibles con Slack y con GitHub |

`execute` acepta las [partes del mensaje](messages.md) y además:

- `username` y `avatar_url`, que se muestran en lugar de los del propio webhook para este mensaje;
- `thread_id`, para publicar en un hilo del canal; los métodos de mensajes también lo aceptan;
- `thread_name`, para iniciar un hilo en un canal foro o multimedia, con `applied_tags`;
- `with_components=True`, necesario para usar componentes en un webhook que no pertenece a ninguna aplicación.

Los clientes aceptan `api_url`, `http_settings` y `allowed_mentions` igual que el bot; por defecto, un webhook puede mencionar usuarios y roles, pero no `@everyone`.

Cuando Discord responde 404 Unknown Webhook, el webhook se eliminó. bostie responde entonces a cada solicitud posterior para él con el mismo 404 sin enviarla, así un webhook muerto no cuenta para el [límite de solicitudes inválidas](rest.md#rate-limits-retries-and-timeouts).

## Administrar webhooks con un bot { #managing-webhooks-with-a-bot }

`bot.rest.webhooks` crea y enumera los webhooks de un canal o de un servidor, lo que necesita el permiso Administrar webhooks, y también tiene los métodos con token:

- `create(channel_id, name=..., avatar=File(...))` devuelve un `Webhook` cuya `url` le puedes pasar a un `WebhookClient`.
- `list(channel_id)`, `list_guild(guild_id)`, `fetch(webhook_id)`, `edit(webhook_id, ...)` y `delete(webhook_id)` funcionan con el token del bot.
- `execute(webhook_id, token, ...)` y los demás métodos `_with_token` y de mensajes son los que llama `WebhookClient`.

El evento `WebhooksUpdate` indica que cambiaron los webhooks de un canal. Un mensaje que envió un webhook llega como un `WebhookMessage`, cuyo `author` es un `PartialUser` y cuyo `webhook_id` está definido.
