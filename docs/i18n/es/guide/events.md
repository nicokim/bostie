---
source: 0402229d2b242ba10aed929b9dedd0087f57b406
description: "Escucha eventos del gateway de Discord con listeners tipados, espéralos o léelos en un stream, usa eventos sin procesar o propios y mira qué intents requieren."
---

# Eventos

![Bostie saluda a una campana que suena](../assets/mascot/bostie-events.webp){ align=right width="180" loading=lazy }

Discord le cuenta al bot lo que pasa mediante eventos del gateway: se envió un mensaje, se unió un miembro, cambió un rol. Un listener es una función asíncrona que recibe un evento.

```python
--8<-- "docs/snippets/events/listeners.py"
```

`@bot.listen` lee la anotación de tipo del primer parámetro y registra la función para esa clase de evento, o para cada clase de una unión. `@bot.listen(MessageUpdate)` indica las clases en su lugar, para una función sin anotación o con una más amplia. Las clases están en `bostie.events`, y la [lista de eventos](../reference/event-list.md) nombra cada una con sus intents y sus campos. Un [router](routers.md) tiene el mismo decorador.

Cada listener se ejecuta como su propia tarea, así que un listener lento no frena a los demás ni al evento siguiente. La tarea empieza de inmediato: el código de un listener hasta su primer `await` se ejecuta antes de que se maneje el evento siguiente. Los listeners de `Starting` y `Stopped` son la excepción: `start()` y `close()` los esperan uno después del otro. `bot.remove_listener(function)` quita el registro de una función de todos los eventos que escucha.

## Solo lo que escuchas { #only-what-you-listen-to }

bostie le pide a su núcleo en Rust que reenvíe solo los nombres de dispatch que usa algo del bot: tus listeners, la caché, el enrutamiento de comandos y componentes, y las esperas pendientes. Cualquier otro evento se descarta antes de llegar a Python, así que no escuchar nada no cuesta nada. Los eventos tipados se decodifican una vez por dispatch, y solo cuando alguien los necesita.

## Intents { #intents }

Discord envía la mayoría de los eventos solo a un bot que pidió sus intents. `Bot(intents=...)` vale `Intents.DEFAULT` por defecto, todos los intents salvo los tres privilegiados. Cuando un listener se registra para un evento que no llega con ninguno de los intents del bot, bostie deja una advertencia en el log con el intent que hay que agregar, y te recuerda que uno privilegiado también hay que activarlo en el Developer Portal. [Intents](gateway.md#intents) los cubre en detalle.

## Valores anteriores y la caché { #old-values-and-the-cache }

Los eventos de actualización traen `old`, el objeto tal como lo tenía la [caché](cache.md) antes del cambio, y los eventos de eliminación traen el objeto eliminado: `RoleUpdate.old`, `ScheduledEventUpdate.old`, `MessageDelete.message`, `MemberRemove.member`. Son `None` cuando la caché no tenía el objeto. Los mensajes solo se guardan en caché con `Bot(max_messages=...)`, los miembros solo con `members=True` y las presencias solo con `presences=True`.

## Esperar un evento { #waiting-for-an-event }

```python
--8<-- "docs/snippets/events/waiting.py"
```

`await bot.wait_for(Event, check=None)` devuelve el siguiente evento de esa clase para el que `check` devuelva verdadero. `async with bot.stream(Event, check=None) as events:` pone en cola cada evento que coincide mientras se ejecuta el bloque, para usarlo con `async for`; los eventos que llegan mientras se ejecuta el cuerpo del bucle no se pierden. Ninguno de los dos tiene un tiempo límite propio, así que ponles un límite con `asyncio.timeout()`. Una excepción lanzada por `check` se lanza desde la espera. `bot.wait_for_component` y `bot.wait_for_modal` hacen lo mismo para los [usos de componentes](components.md#waiting-for-a-click-inside-a-handler) y los [envíos de modales](modals.md#waiting-for-the-submit-inside-a-handler).

## Eventos sin procesar y eventos que bostie no modela { #raw-events-and-events-bostie-does-not-model }

```python
--8<-- "docs/snippets/events/custom_events.py"
```

`@bot.listen_raw("NAME", ...)` entrega un `RawEvent` por cada nombre de dispatch que indiques, esté modelado o no, y un listener anotado con `RawEvent` recibe todos los dispatch. Un `RawEvent` tiene el `name` del dispatch, su número de secuencia `seq` y el payload como `raw` (un `dict` nuevo cada vez) o `raw_json` (bytes). Todos los eventos tipados también tienen `raw` y `raw_json`.

Cuando Discord envía un payload que no encaja con su clase, por ejemplo sin un campo que la documentación marca como obligatorio, bostie deja una advertencia en el log una vez por nombre de evento, y los listeners tipados se saltan ese evento mientras que los listeners de `RawEvent` lo siguen recibiendo.

## Eventos propios { #events-of-your-own }

Crea una subclase de `BotEvent`, con `kw_only=True`, y envía una instancia con `bot.dispatch(event)`: sus listeners, esperas y streams la reciben como cualquier otro evento. Una subclase sin `NAME` se nombra según su módulo y su clase. Las clases de eventos son structs inmutables de `msgspec`, así que sus campos se declaran como anotaciones.

## Eventos del ciclo de vida y de los shards { #lifecycle-and-shard-events }

bostie envía eventos propios sobre el bot: `Starting`, `Started`, `Stopping` y `Stopped` alrededor del [ciclo de vida](running.md#the-lifecycle); `ShardReady`, `ShardDisconnect`, `ShardStop` y `ShardBackpressure` sobre cada [shard](sharding.md); `ListenerError` y `TaskError` sobre los fallos. Las conexiones de voz envían [eventos de voz](../voice/index.md#events) por el mismo `@bot.listen`.

## Cuando un listener lanza una excepción { #when-a-listener-raises }

Una excepción en un listener se envía como un evento `ListenerError` con el `listener`, el `event` y el `error`. Sin un listener de `ListenerError`, bostie registra la excepción en el log con su traceback; si hay uno, el logging queda a tu cargo. Una excepción en un listener de `ListenerError` solo se registra en el log.
