---
source: 9715861debf2a59f49301a4b9f3800dc2c8262dd
description: "Qué guarda la caché de bostie del gateway, cómo buscar servidores, miembros y canales, recurrir a REST y calcular los permisos y la jerarquía de roles."
---

# Caché y permisos

![Bostie deja caer un hueso en una caja](../assets/mascot/bostie-cache.webp){ align=right width="180" loading=lazy }

`bot.cache` guarda lo que el gateway le contó al bot, para que los handlers puedan buscar cosas sin hacer una solicitud. Se llena a partir de los eventos, nunca de las llamadas REST que haces.

```python
--8<-- "docs/snippets/cache/cache_lookups.py"
```

## Qué guarda { #what-it-keeps }

| Se guarda | Por defecto | Opción |
|---|---|---|
| los servidores, con sus roles, emojis y stickers | sí | |
| los canales y los hilos activos | sí | |
| los estados de voz | sí | |
| los eventos programados, las instancias de escenario y los sonidos del panel de sonidos | sí | |
| el usuario del bot y su propio miembro en cada servidor | sí | |
| todos los miembros | no | `members=True`, necesita `Intents.GUILD_MEMBERS` |
| las presencias de los miembros que no están desconectados | no | `presences=True`, necesita `Intents.GUILD_PRESENCES` |
| los mensajes más recientes | no | `max_messages=N` |
| los canales de MD más recientes | no | `max_dm_channels=N` |

`Bot(cache=False)` no guarda nada, y entonces las demás opciones lanzan `ValueError`. Una opción cuyo intent falta lanza `MissingIntent` cuando se crea el `Bot`. Los canales se guardan tal como los envió Discord, salvo los campos que `CHANNEL_UPDATE` no mantiene actualizados (`last_message_id`, `message_count`, `member_count`, `last_pin_timestamp`), que son `None`.

Con `members=True`, Discord envía solo algunos miembros de los servidores grandes cuando el bot se conecta. bostie pide entonces el resto, un servidor a la vez por shard, y un shard cuenta como listo solo cuando sus servidores están completos. `chunk_members=False` omite eso, y la caché se va llenando a medida que los miembros se unen, cambian o hablan. Las solicitudes que reciben un rate limit se reintentan después del retraso que indica Discord.

## Búsquedas { #lookups }

| Método | Devuelve |
|---|---|
| `guild(id)`, `guilds()` | un `Guild` o todos |
| `channel(id)`, `guild_channels(guild_id)` | un `Channel`, o los canales de un servidor con sus hilos activos |
| `role(id)`, `guild_roles(guild_id)` | un `Role`, o los roles de un servidor |
| `voice_state(guild_id, user_id)`, `guild_voice_states(guild_id)` | dónde están los usuarios en voz; `None` cuando el usuario no está en ningún canal de voz |
| `scheduled_event(id)`, `guild_scheduled_events(guild_id)` | un `ScheduledEvent`, o los de un servidor |
| `stage_instance(id)`, `guild_stage_instances(guild_id)` | una `StageInstance` en vivo, o las de un servidor |
| `soundboard_sound(sound_id)`, `guild_soundboard_sounds(guild_id)` | un `SoundboardSound`, o los de un servidor |
| `me(guild_id)` | el propio `Member` del bot en un servidor |
| `member(guild_id, user_id)`, `guild_members(guild_id)` | miembros; solo el del propio bot, salvo que `members=True` |
| `presence(guild_id, user_id)`, `guild_presences(guild_id)` | presencias, con `presences=True`; `None` si el usuario está desconectado |
| `user(id)` | el usuario del bot, o el usuario de un miembro o canal de MD en caché |
| `dm_channel(user_id)` | el canal de MD del bot con un usuario, si se guarda |
| `message(id)` | uno de los `max_messages` mensajes más recientes |
| `permissions(channel_id, member=None)` | los permisos de un miembro en un canal, los del bot si no se indica `member` |
| `guild_permissions(guild_id, member=None)` | lo mismo, en todo el servidor |

Las búsquedas devuelven `None`, o una lista vacía, para lo que la caché no contiene. `bot.cache.clear()` la vacía. Los eventos de actualización y de eliminación traen el objeto en caché de antes del cambio, como explica [Eventos](events.md#old-values-and-the-cache).

## La caché o, si no, REST { #the-cache-else-rest }

```python
--8<-- "docs/snippets/cache/lookups_with_rest.py"
```

`bot.lookup` devuelve un objeto de la caché cuando lo tiene, y si no, se lo pide a REST: `user(user_id)`, `member(guild_id, user_id)`, `channel(channel_id)`, `guild(guild_id)`, `role(guild_id, role_id)` y `message(channel_id, message_id)`. Lo que devuelve REST no se guarda: la caché sigue atendiendo solo al gateway, así que una segunda búsqueda del mismo objeto ausente vuelve a preguntar. Los errores de REST pasan tal cual, como `NotFound` para un id que no existe.

## Eventos programados, escenarios y sonidos { #scheduled-events-stages-and-sounds }

```python
--8<-- "docs/snippets/cache/guild_events.py"
```

Llegan con cada servidor y siguen sus eventos de creación, actualización y eliminación, que necesitan `Intents.GUILD_SCHEDULED_EVENTS` para los eventos programados y `Intents.GUILD_EXPRESSIONS` para los sonidos del panel de sonidos; los dos están en `Intents.DEFAULT`. La respuesta a `bot.request_soundboard_sounds` reemplaza los sonidos de un servidor. Sus eventos de actualización traen `old`, y `SoundboardSoundDelete` trae el `sound` eliminado.

## Arranque { #startup }

Un shard está listo cuando cada servidor que enumeró su `READY` llegó como un `GuildCreate`, o cuando no llegó ningún servidor durante `guild_ready_timeout` segundos, 2 por defecto. `Started` espera a todos los shards. Un `GuildCreate` con `joined=True` es un servidor al que se acaba de agregar el bot; los demás son servidores que pasan a estar disponibles.

`large_threshold`, de 50 a 250 y 50 por defecto, es la cantidad de miembros a partir de la cual Discord envía un servidor sin sus miembros desconectados.

## Permisos { #permissions }

`cache.permissions(channel_id, member)` aplica las reglas de Discord: el propietario del servidor y los administradores tienen todo; si no, el rol `@everyone`, luego los roles del miembro y luego las sobrescrituras del canal para `@everyone`, para los roles y para el miembro; un miembro aislado temporalmente conserva solo Ver canal y Leer el historial de mensajes; sin Ver canal no hay nada; sin Enviar mensajes desaparecen los permisos que lo necesitan; en un canal de voz sin Conectar, desaparecen los permisos de voz. Un hilo usa las sobrescrituras de su canal padre. Devuelve `None` cuando a la caché le falta el canal, el servidor o el canal padre de un hilo.

El mismo cálculo está disponible sin la caché: `bostie.permissions.compute_permissions(guild, member, channel=None, *, parent=None)`, donde `parent` es el canal padre de un hilo. Ese módulo también contiene conjuntos de `Permissions` tomados de la documentación de Discord: `ALL`, `NONE`, `TEXT`, `VOICE` y `STAGE` (los permisos que se aplican a cada tipo de canal), `ELEVATED` (los que necesitan la autenticación en dos pasos en la cuenta del moderador cuando el servidor la exige) y `TIMED_OUT` (los que conserva un miembro aislado temporalmente).

Dentro de una interacción no hace falta la caché: `ctx.interaction.app_permissions` contiene los permisos del bot en el canal, y `ctx.member.permissions`, los del usuario.

## Jerarquía de roles { #role-hierarchy }

Un miembro solo puede expulsar, banear, aislar temporalmente o editar a los miembros cuyo rol más alto está por debajo del suyo, y Discord muestra el nombre de un miembro con el color de su rol con color más alto. `bostie.permissions` tiene las tres funciones auxiliares, que aceptan el `Guild` y los miembros de la caché, de una interacción o de REST:

| Función auxiliar | Devuelve |
|---|---|
| `top_role(guild, member)` | el `Role` más alto del miembro, o `None` cuando solo tiene `@everyone` |
| `display_colors(guild, member)` | los `RoleColors` de su rol más alto con color, o `None` |
| `can_act_on(guild, actor, target)` | si `actor` está por encima de `target`, un miembro o un rol: el propietario está por encima de todos, nadie está por encima del propietario y, si no, el rol más alto del actor se compara por posición con el rol más alto del objetivo, o con el rol mismo, como hace Discord para la gestión de roles |

Los roles en la misma posición se ordenan por id, primero el más antiguo, como los ordena Discord.

```python
--8<-- "docs/snippets/cache/role_hierarchy.py"
```
