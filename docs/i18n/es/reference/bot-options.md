---
source: b62f5277d6f68b2edc4dafd4ea9b8084ddfffea0
description: "Cada opción de la clase Bot de bostie y su valor por defecto: intents, presencia, sharding, sincronización, diferimiento automático, caché, HTTP, voz y apagado."
---

# Opciones de Bot

Todas las opciones de `Bot(...)`, todas solo por palabra clave. Las guías explican cada una en contexto.

## Gateway e identidad { #gateway-and-identity }

| Opción | Por defecto | Significado |
|---|---|---|
| `intents` | `Intents.DEFAULT` | los [intents](../guide/gateway.md#intents) que se piden; el valor por defecto tiene todos los intents salvo los tres privilegiados |
| `status` | `Status.ONLINE` | el estado de [presencia](../guide/gateway.md#presence) al iniciar sesión |
| `activities` | `()` | las actividades al iniciar sesión, construidas con `bostie.activity` |
| `afk` | `False` | si el bot se informa como ausente |
| `idle_since` | `None` | el `datetime` en el que el bot quedó inactivo |
| `large_threshold` | 50 | de 50 a 250: la cantidad de miembros a partir de la cual Discord deja afuera del payload de un servidor a los miembros desconectados |
| `shard_count` | `"auto"` | la cantidad de [shards](../guide/sharding.md), o `"auto"` para la recomendación de Discord |
| `shard_ids` | `"all"` | los shards que ejecuta este proceso; necesita un `shard_count` explícito |
| `before_identify` | `None` | `async def f(shard_id)`, que se espera antes de cada identificación, para coordinar procesos |
| `session_file` | `None` | una ruta donde `close()` guarda las sesiones y el siguiente `start()` las [reanuda](../guide/sharding.md#resuming-after-a-restart) |
| `guild_ready_timeout` | 2.0 | segundos sin un servidor nuevo después de los cuales un shard cuenta como [listo](../guide/cache.md#startup) |

## Comandos e interacciones { #commands-and-interactions }

| Opción | Por defecto | Significado |
|---|---|---|
| `auto_sync` | `True` | [sincroniza los comandos](../guide/registration.md) al arrancar cuando son distintos de los de Discord |
| `dev_guilds` | `()` | ids de servidores donde registrar los comandos en lugar de hacerlo de forma global |
| `contexts` | `None` | dónde se pueden usar los comandos globales, salvo que un comando o un router indique otra cosa |
| `integration_types` | `None` | qué instalaciones incluyen los comandos globales, salvo que un comando o un router indique otra cosa |
| `translate` | `None` | `f(text, locale)`, que devuelve una [traducción](../guide/registration.md#translations) o `None` |
| `auto_defer` | 2.5 | segundos tras los cuales una interacción sin respuesta se [difiere](../guide/responses.md#deferring); `None` lo desactiva |
| `allowed_mentions` | `AllowedMentions(parse=("users", "roles"))` | qué menciones pueden [notificar](../guide/responses.md#allowed-mentions) por defecto |
| `owner_ids` | `None` | los usuarios que deja pasar [`is_owner`](../guide/checks.md#roles-and-owners); `None` lee el propietario o el equipo de la aplicación |

## Caché { #cache }

| Opción | Por defecto | Significado |
|---|---|---|
| `cache` | `True` | mantiene la [caché](../guide/cache.md); `False` no guarda nada |
| `members` | `False` | guarda todos los miembros; necesita `Intents.GUILD_MEMBERS` |
| `chunk_members` | `True` | con `members=True`, pide al arrancar los miembros que Discord deja afuera |
| `presences` | `False` | guarda las presencias; necesita `Intents.GUILD_PRESENCES` |
| `max_messages` | 0 | cuántos de los mensajes más recientes guardar |
| `max_dm_channels` | 0 | cuántos de los canales de MD más recientes guardar |

## HTTP, voz y apagado { #http-voice-and-shutdown }

| Opción | Por defecto | Significado |
|---|---|---|
| `http_settings` | `HTTPSettings()` | los [rate limits, reintentos, tiempos de espera y proxy](../guide/rest.md#rate-limits-retries-and-timeouts) de las solicitudes REST |
| `api_url` | `None` | la URL base de REST; si no, `BOSTIE_API_URL`, y si no, la de Discord |
| `voice` | `voice.Defaults()` | los [ajustes de voz](../voice/index.md#settings) con los que arranca cada conexión |
| `shutdown_grace` | 0.0 | segundos que [`close()`](../guide/running.md#the-lifecycle) les da a los handlers en ejecución antes de cancelarlos |

`run(token=None, loop_factory=None)` y `start(token=None)` aceptan el token, `BOSTIE_TOKEN` por defecto.
