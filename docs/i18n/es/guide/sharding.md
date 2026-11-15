---
source: c45c92d8551bab86affc3711ec78970f48e886d8
---

# Sharding y reanudación

![Tres Bosties corren en fila](../assets/mascot/bostie-running.webp){ align=right width="220" loading=lazy }

Discord reparte los servidores de un bot entre conexiones al gateway llamadas shards: el servidor `g` pertenece al shard `(g >> 22) % shard_count`. Un bot en más de 2500 servidores tiene que usar sharding; Discord cierra una conexión sin shards con el código 4011 y `start()` lanza `ShardingRequired`.

## Shards en un proceso { #shards-in-one-process }

`Bot(shard_count="auto")`, el valor por defecto, le pregunta a Discord cuántos shards usar y los ejecuta todos. No hace falta nada más: bostie inicia los shards dentro de los límites de identificación de Discord, y cada shard envía sus eventos a los mismos listeners, la misma caché y los mismos handlers. `event.shard_id` indica por qué shard llegó un evento.

## Shards en varios procesos { #shards-across-processes }

```python
--8<-- "docs/snippets/sharding/cluster.py"
```

`shard_count=16` fija el total, y `shard_ids` elige los shards que ejecuta este proceso; `shard_ids` necesita un `shard_count` explícito, y sus ids deben ser distintos y estar dentro de `range(shard_count)`.

Discord permite una cantidad limitada de identificaciones a la vez por bot. bostie respeta ese límite dentro de un proceso; entre procesos, `before_identify` las coordina. bostie espera `before_identify(shard_id)` antes de cada identificación, así que puede esperar un turno en un lock que compartan tus procesos. En el ejemplo, `identify_lock` representa ese lock compartido, retenido durante los 5 segundos que Discord pide entre identificaciones de un mismo bucket. Si el hook lanza una excepción, bostie registra el error en el log e identifica de todos modos.

## Eventos de los shards { #shard-events }

| Evento | Campos | Se envía cuando |
|---|---|---|
| `ShardReady` | `shard_id` | la sesión nueva de un shard está lista y llegaron sus servidores |
| `ShardDisconnect` | `shard_id`, `code`, `reason`, `resuming` | se cortó una conexión; con `resuming`, el shard reanuda su sesión y vuelve a reproducir los eventos que se perdió; si no, inicia una sesión nueva |
| `ShardStop` | `shard_id`, `code`, `reason` | un shard se detuvo de forma definitiva, porque se ejecutó `close()` o porque Discord lo terminó |
| `ShardBackpressure` | `shard_id`, `queued_bytes` | un shard dejó de leer porque el event loop se está quedando atrás |

Las reconexiones y las reanudaciones no necesitan nada de tu parte. `bot.latency` es el promedio del tiempo de ida y vuelta del heartbeat de todos los shards, y `bot.gateway.shard(shard_id).latency`, el de un shard.

## Reanudar después de un reinicio { #resuming-after-a-restart }

Un reinicio normalmente inicia una sesión nueva en cada shard, y se pierden los eventos de los minutos intermedios. Con `Bot(session_file="sessions.json")`, `close()` mantiene abiertas las sesiones y las escribe en el archivo. El siguiente `start()` lee el archivo, lo borra y reanuda cada sesión guardada que coincide con el `shard_count` y los ids de shard actuales, así que Discord vuelve a reproducir los eventos que ocurrieron mientras el bot estaba caído. Un archivo que no se puede leer se registra en el log y se ignora, y los shards sin una sesión guardada se identifican.

`bot.run()` se cierra con Ctrl+C y SIGTERM, así que un reinicio hecho por un gestor de procesos o por un despliegue guarda las sesiones. Un fallo inesperado no las guarda, y el siguiente arranque se identifica. Una interacción que se vuelve a reproducir más de 5 segundos después de crearse se omite con una advertencia, porque ya no se puede responder a su token a tiempo.
