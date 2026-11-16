---
source: 013ae18bd66c3357e9ff74c65156f6ac85917bf1
description: "Llama a la API REST de Discord: paginación, purgar mensajes, registro de auditoría, errores, rate limits, reintentos, descargas y solicitudes sin procesar."
---

# API REST

![Bostie salta para atrapar una pelota](../assets/mascot/bostie-jumping.webp){ align=right width="180" loading=lazy }

`bot.rest` tiene un método para cada operación de la API REST de Discord que puede llamar un bot, agrupados por el recurso sobre el que actúan: `bot.rest.messages.create`, `bot.rest.members.add_role`, `bot.rest.guilds.fetch`. Las solicitudes pasan por el cliente HTTP de bostie escrito en Rust, que respeta los rate limits de Discord.

```python
--8<-- "docs/snippets/rest/rest_calls.py"
```

## Convenciones { #conventions }

- Los ids de la URL van primero, solo posicionales, en el orden de la URL: `bot.rest.members.add_role(guild_id, user_id, role_id)`.
- Todo lo demás es un argumento por palabra clave con el nombre que usa la documentación de Discord, comprobado contra un `TypedDict` por operación, así que un error de tipeo es un error de tipos y un `TypeError` en tiempo de ejecución.
- Las operaciones que Discord registra en el registro de auditoría aceptan `reason=`, de 1 a 512 caracteres, que ven los moderadores del servidor.
- Un campo de imagen o de sonido, como el `image` de un emoji o el `icon` de un servidor, acepta un `File`; bostie lo envía como la data URI que espera Discord. Los campos de permisos aceptan `Permissions`.
- Un emoji, como el que agrega `reactions.create`, acepta un emoji Unicode, un emoji personalizado como `<:name:id>`, `<a:name:id>` o `name:id`, o un modelo `Emoji` o `PartialEmoji`.
- Los métodos llamados `fetch` obtienen un objeto, `list` recorre varios, `edit` modifica, `create` crea y `delete` elimina. Los demás verbos conservan la palabra de la documentación, como `members.add_role` o `guilds.begin_prune`.
- Los métodos devuelven modelos, o `None` cuando Discord responde sin contenido. No cambian `bot.cache`, salvo que `channels.create_dm` guarda el canal de MD cuando `max_dm_channels` lo permite; los eventos del gateway que siguen a un cambio actualizan la caché.

## Paginación { #paging }

Los métodos `list` de los endpoints que Discord pagina devuelven un iterador asíncrono, que obtiene la página siguiente a medida que avanzas: `async for message in bot.rest.messages.list(channel_id, limit=500):`, como hace `/today` arriba. Sin `limit` recorre todos los elementos. `before`, `after` y `around` empiezan en otro lugar, como máximo uno de ellos; aceptan un id o un `datetime`, que bostie convierte en el id de ese momento. Los mensajes llegan del más nuevo al más antiguo, o del más antiguo al más nuevo con `after`. Un método `list` de un endpoint que Discord no pagina es una corrutina que devuelve una tupla. `audit_log.list` produce objetos `AuditLogPage` completos, con los usuarios, webhooks y demás objetos a los que hacen referencia las entradas.

## Purgar mensajes { #purging-messages }

`messages.purge(channel_id, limit=100, check=None)` recorre los últimos `limit` mensajes, del más nuevo al más antiguo, y elimina aquellos para los que `check` devuelve verdadero, o todos si no hay `check`; `limit=None` recorre el canal entero, y `before` y `after` empiezan en otro lugar, como en `list`. Los mensajes de menos de 14 días se eliminan en lotes de hasta 100, y los más antiguos con una solicitud cada uno, porque Discord solo elimina en lote los mensajes recientes; un tipo que Discord no permite eliminar, como el mensaje inicial de un hilo, se omite. `bulk=False` elimina de a uno, como en un MD, donde la eliminación en lote no existe. Devuelve los mensajes eliminados, y `reason` va al registro de auditoría. La eliminación en lote y los mensajes de otros necesitan el permiso Administrar mensajes.

```python
--8<-- "docs/snippets/rest/purge.py"
```

## El registro de auditoría { #the-audit-log }

`audit_log.list(guild_id)` produce objetos `AuditLogPage`, y el gateway envía cada entrada nueva como un evento `AuditLogEntryCreate`. Los `changes` de una entrada contienen un `AuditLogChange` por campo cambiado, cuyos `new_value` y `old_value` tienen el tipo que el objeto cambiado le da a ese campo: `Permissions` para los `permissions` de un rol, un `datetime` para el `communication_disabled_until` de un miembro, una tupla de `Overwrite` para los `permission_overwrites` de un canal. Las excepciones de Discord siguen su documentación: `$add` y `$remove` sobre los roles de un miembro contienen objetos `AuditLogRole`, el `avatar_hash` de un webhook y el `channel_id` de una invitación toman el tipo de `avatar` y de un id, y un cambio en los permisos de un comando usa como clave el id del rol, del usuario o del canal y contiene un `CommandPermission`. Una clave que bostie no conoce, o un valor con una forma que el campo no acepta, conserva su valor JSON, y un valor que Discord omite es `None`.

```python
--8<-- "docs/snippets/rest/audit_log.py"
```

## Errores { #errors }

Una solicitud que Discord responde con un código de error lanza una `HTTPException`: `BadRequest` para 400, `Forbidden` para 403, `NotFound` para 404, `ServerError` para 5xx. La excepción tiene el `status`, el `code` de Discord como `JSONErrorCode`, su `message`, los `errors` por campo y el `body` sin procesar; su texto muestra cada error de campo en su propia línea. `UnexpectedResponse` significa que Discord aceptó la solicitud pero su respuesta no se pudo decodificar; su mensaje dice si el cambio se aplicó, y `error.raw` contiene la respuesta.

## Rate limits, reintentos y tiempos de espera { #rate-limits-retries-and-timeouts }

El cliente de Rust lleva la cuenta del bucket de rate limit de cada ruta y del límite global de Discord, y retiene una solicitud hasta que su bucket tiene lugar. `Bot(http_settings=HTTPSettings(...))` cambia cómo:

| Campo de `HTTPSettings` | Por defecto | Significado |
|---|---|---|
| `global_rate_limit` | 50 | solicitudes por segundo entre todas las rutas; súbelo si Discord le dio al bot un límite más alto |
| `max_rate_limit_wait` | 30.0 | la espera más larga por un rate limit; una solicitud que esperaría más lanza `RateLimited` sin enviarse |
| `retries` | 2 | cuántas veces se reintenta una solicitud fallida, con un backoff exponencial que espera como máximo `max_rate_limit_wait` |
| `connect_timeout` | 10.0 | segundos para abrir una conexión |
| `read_timeout` | 60.0 | segundos de espera por una respuesta |
| `rate_limiter` | `True` | `False` envía de inmediato y solo espera los 429 que vuelven, para usarlo detrás de un proxy de rate limits |
| `identify_spacing` | 5.0 | segundos entre dos identificaciones en el gateway, y antes de volver a identificarse tras el código de cierre 4007; Discord exige 5, así que redúcelo solo contra `bostie.testing` |
| `proxy` | `None` | la URL de un proxy HTTP o HTTPS para todas las solicitudes, incluido el audio de `voice.Url` y las descargas de la CDN, con credenciales incluidas como en `http://user:password@host:3128` |

Un 429 se reintenta después del retraso que indica Discord, si está dentro de `max_rate_limit_wait`. Un 500, 502, 503, 504 o 524, o una conexión que se cortó, se reintenta `retries` veces para todos los métodos salvo `POST`, que podría haberse aplicado; una conexión que nunca se abrió se reintenta para todos los métodos.

Discord bloquea una IP que envía 10 000 solicitudes inválidas (401, 403, 429) en 10 minutos. bostie las cuenta y deja una advertencia en el log a las 1000, 5000 y 9000. A las 9500 rechaza cada solicitud con `RequestBudgetExhausted` en lugar de enviarla, hasta que la cuenta de los últimos 11 minutos baja de 9000.

## Cualquier endpoint { #any-endpoint }

`bot.http.request(method, route, **params)` envía cualquier solicitud por el mismo cliente y devuelve el JSON decodificado, o `None` para una respuesta vacía. La ruta conserva sus marcadores de posición, que completan los argumentos por palabra clave: `await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=123)`. Acepta `json=`, `files=` para una subida multipart, `form=` para campos de formulario con nombre, `urlencoded=` para un cuerpo form-urlencoded, `query=` y `reason=`, y lanza las mismas excepciones. `bot.http.send(...)` acepta los mismos argumentos y devuelve los bytes sin procesar de la respuesta, para un endpoint que no responde con JSON.

## Descargar archivos { #downloading-files }

Los archivos adjuntos y las imágenes viven en la CDN de Discord, no detrás de la API, así que una descarga no envía ningún token ni consume rate limit. `bot.http` los obtiene por el mismo cliente de Rust, con sus tiempos de espera, reintentos y `proxy`:

| Método | Qué hace |
|---|---|
| `read(source)` | devuelve los bytes |
| `save(source, path)` | los escribe en el archivo de `path` |
| `to_file(source)` | devuelve un `File` para volver a subirlo; a partir de un `Attachment` conserva el nombre del archivo, la descripción, el título, el spoiler y los datos del mensaje de voz, y a partir de una URL lo nombra la última parte de su ruta |

`source` es un `Attachment`, o una URL como las que construyen las funciones auxiliares de [`bostie.cdn`](formatting.md). Un archivo que no existe lanza `NotFound`.

```python
--8<-- "docs/snippets/rest/downloads.py"
```

## Sin un bot { #without-a-bot }

Un script o un backend web que solo necesita REST usa un `RESTClient`:

```python
--8<-- "examples/rest_script.py"
```

`async with RESTClient(token) as client:` abre el cliente HTTP; `client.rest` tiene todos los grupos de `bot.rest`, y `client.http` el cliente sin procesar. El token es `BOSTIE_TOKEN` por defecto, y el cliente acepta `api_url`, `http_settings` y `allowed_mentions` igual que el bot. La [guía de OAuth2](oauth2.md) cubre `BearerClient`, que llama a la API con el token de un usuario, y la [guía de webhooks](webhooks.md), los clientes que no necesitan ningún token.

## Los grupos { #the-groups }

| `bot.rest.` | Métodos |
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

Los parámetros y el tipo de retorno de cada método están en la [referencia de la API REST](../reference/api/rest.md). Algunas operaciones se dejan a propósito para `bot.http.request`: los endpoints que Discord dejó obsoletos o nunca documentó, y los endpoints de callback de la interacción y de la respuesta original, que cubren los [contextos de interacción](responses.md).

Algunos grupos sirven funciones de Discord que van más allá de los bots en servidores: `monetization` y `application` para la [monetización y las actividades](platform.md), `lobbies`, `identities` y `partner_sdk` para el [Social SDK](platform.md#social-sdk), y `application.update_role_connection_metadata` para los [roles vinculados](oauth2.md#linked-roles).
