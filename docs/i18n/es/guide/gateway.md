---
source: 3337dc9b9c1508c500eac3639bbfb9fc948f4a27
---

# Intents, presencia y el gateway

El gateway es el WebSocket por el que Discord envía eventos y el bot envía su presencia y sus solicitudes. El núcleo en Rust de bostie lo mantiene conectado: heartbeats, reanudaciones, reconexiones, compresión y el rate limit de envío. Esta página cubre lo que el bot le pide.

## Intents { #intents }

Discord envía un evento solo a un bot que pidió su intent. `Bot(intents=...)` acepta un flag `Intents`, `Intents.DEFAULT` si no se indica: todos los intents salvo los tres privilegiados.

| Intent privilegiado | Se necesita para |
|---|---|
| `GUILD_MEMBERS` | los eventos de unión, actualización y salida de miembros, todos los miembros en la caché, pedir todos los miembros |
| `GUILD_PRESENCES` | las actualizaciones de presencia y las presencias en la caché |
| `MESSAGE_CONTENT` | el contenido, los embeds, los archivos adjuntos y los componentes de los mensajes en servidores, salvo los mensajes que mencionan al bot; en los MD no hace falta ningún intent para eso |

Un intent privilegiado también hay que activarlo en la página **Bot** del Developer Portal. Si no, Discord cierra la conexión con el código 4014 y `start()` lanza `PrivilegedIntentsRequired`, cuyo mensaje dice dónde activarlo. Agrega intents con `|`: `Intents.DEFAULT | Intents.MESSAGE_CONTENT`. bostie advierte cuando un listener necesita un intent que el bot no tiene, y lanza `MissingIntent` cuando lo necesita una opción o una solicitud.

La voz necesita `GUILD_VOICE_STATES`, que está incluido en `DEFAULT`.

```python
--8<-- "docs/snippets/gateway/intent_presets.py"
```

Junto a los intents individuales hay tres combinaciones predefinidas: `Intents.DEFAULT`, que también se llama `Intents.UNPRIVILEGED`, contiene todos los intents salvo los privilegiados; `Intents.PRIVILEGED`, los tres privilegiados; e `Intents.ALL`, todos. `& ~` le quita intents a una combinación, como arriba.

## Presencia { #presence }

```python
--8<-- "docs/snippets/gateway/presence_and_requests.py"
```

`Bot(status=..., activities=[...], afk=False, idle_since=None)` define lo que muestra el bot cuando se conecta. `await bot.update_presence(...)` lo cambia en todos los shards, y en los que se conecten después; una opción que no se pasa conserva su valor.

`status` es un `Status`: `ONLINE`, `IDLE`, `DND` o `INVISIBLE`. `bostie.activity` construye las actividades:

| Builder | Muestra |
|---|---|
| `activity.playing(name)` | Jugando a *name* |
| `activity.listening(name)` | Escuchando *name* |
| `activity.watching(name)` | Viendo *name* |
| `activity.competing(name)` | Compitiendo en *name* |
| `activity.streaming(name, url)` | Transmitiendo *name*, para los servicios de streaming que admite Discord |
| `activity.custom(text)` | un estado personalizado con *text* |

Todos salvo `custom` aceptan `state=`, una segunda línea de texto, y devuelven un `activity.ActivityDict`.

## Solicitudes { #requests }

Algunos datos solo llegan por el gateway, como respuesta a una solicitud:

- `await bot.request_members(guild_id, query="ab", limit=10)` devuelve los miembros cuyo nombre de usuario o apodo empieza con `query`, como máximo `limit` (de 0 a 100). `user_ids=[...]` pide en su lugar hasta 100 miembros por id. Sin ninguno de los dos, pide todos los miembros, lo que necesita `GUILD_MEMBERS`. `presences=True` agrega sus presencias y necesita `GUILD_PRESENCES`. La respuesta es un `RequestedMembers` con `members`, `presences` y `not_found`, los ids que Discord no encontró. Discord le aplica un rate limit a esta solicitud: una rechazada lanza `MemberRequestRateLimited` con `retry_after`.
- `await bot.request_soundboard_sounds(guild_id)` devuelve los sonidos del panel de sonidos del servidor.
- `await bot.request_channel_info(guild_id)` devuelve el estado de voz y la hora de inicio de la voz de los canales del servidor; `fields=["status"]` pide menos.

Cada una espera hasta 30 segundos por cada parte de la respuesta, `timeout=` en el caso de los miembros, y después lanza `TimeoutError`. Una solicitud a un shard que está iniciando una sesión nueva lanza `StateError`; vuelve a intentarlo cuando esté listo.

## Comandos del gateway sin procesar { #raw-gateway-commands }

`bot.gateway.send(op, d)` envía cualquier comando del gateway. El opcode 3 (presencia) va a todos los shards; un payload con `guild_id` va al shard de ese servidor, uno con `guild_ids` a cada shard con su parte de los ids, y cualquier otro al shard 0. Las respuestas de Discord llegan como eventos, y solo para los nombres de evento que algo escucha, así que acompaña un comando sin procesar con un listener de `listen_raw` o un `wait_for`.

`bot.gateway` también ofrece `shard_ids`, los shards que ejecuta este proceso; `shard_for(guild_id)`, el shard al que Discord envía los eventos de un servidor; `shard(shard_id)`, con sus propios `send`, `reconnect` y `latency`; `reconnect()`, que corta todas las conexiones y reanuda las sesiones en conexiones nuevas; y `latency`, el promedio del tiempo de ida y vuelta del heartbeat. Un comando para un shard que este proceso no ejecuta lanza `ShardNotRunning`, y uno que queda en cola detrás de demasiados otros lanza `GatewayQueueFull`.

## Contrapresión { #backpressure }

El núcleo en Rust lee los eventos tan rápido como los envía Discord y se los pasa a Python. Cuando el event loop se queda atrás, un shard deja de leer hasta que Python se pone al día, deja una advertencia en el log y envía `ShardBackpressure` con los bytes en espera. La causa es un trabajo bloqueante largo en un handler o un listener; mueve ese trabajo a un hilo con `asyncio.to_thread`.
