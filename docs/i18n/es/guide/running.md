---
source: fecb554b875bf1b19567e85926b3784beca4dba5
---

# Ejecutar un bot

![Bostie celebra con confeti](../assets/mascot/bostie-celebrating.webp){ align=right width="180" loading=lazy }

## `bot.run()` { #botrun }

`bot.run()` es el programa completo para la mayoría de los bots. Configura el logging en INFO por stderr, salvo que el logger raíz ya tenga handlers, inicia un event loop y ejecuta el bot hasta que se detiene. Ctrl+C y, en el hilo principal, SIGTERM lo cierran de forma limpia.

El token sale de la variable de entorno `BOSTIE_TOKEN`, o de `bot.run(token)`. Se quita un `Bot ` al principio, y si falta el token se lanza `ValueError`. `run` también acepta `loop_factory`, una función que crea el event loop, que se le pasa a `asyncio.run`.

## El ciclo de vida { #the-lifecycle }

`bot.start()` hace el trabajo que hay detrás de `run()`:

1. Envía el evento `Starting` y espera a cada uno de sus listeners por turno. Aquí funciona REST, pero no el gateway; un listener que lanza una excepción detiene el arranque.
2. [Sincroniza los comandos](registration.md), salvo que `auto_sync=False`.
3. Conecta los shards del gateway. Cuando todos los shards están listos y llegaron sus servidores, envía `Started` una vez, inicia las [tareas en segundo plano](tasks.md) y deja que `wait_until_ready()` retorne.
4. Maneja eventos hasta `close()`, o hasta que Discord cierra el gateway de forma definitiva, lo que lanza una excepción, por ejemplo `InvalidToken` o `PrivilegedIntentsRequired`.

```python
--8<-- "docs/snippets/running/lifecycle_events.py"
```

`await bot.close()` se apaga en este orden:

1. Si se envió `Started`, envía `Stopping` y les da a sus listeners hasta 5 segundos mientras el gateway sigue abierto.
2. Sale de todos los canales de voz y luego cierra cada shard, lo que termina su sesión con el código de cierre 1000, o la guarda para [reanudarla](sharding.md#resuming-after-a-restart).
3. Cancela las tareas en segundo plano. Los handlers que siguen ejecutándose tienen `shutdown_grace` segundos, 0 por defecto, antes de que también se cancelen.
4. Cierra el cliente HTTP y envía `Stopped`, cuyos listeners espera. `bot.http`, `bot.rest` y `bot.gateway` dejan de existir.

`close()` se puede llamar desde cualquier lugar, más de una vez y mientras se ejecuta `start()`; después el bot se puede volver a iniciar. `async with bot:` lo cierra cuando termina el bloque.

## Dentro de otro programa { #inside-another-program }

Cuando tu programa ya ejecuta un event loop, inicia el bot como una tarea:

```python
--8<-- "docs/snippets/running/inside_asyncio.py"
```

`await bot.wait_until_ready()` retorna una vez que se envió `Started`, de inmediato si ya se envió, y lanza `StateError` si el bot se detiene antes; esperar entonces la tarea de `start()` lanza el motivo. `bot.is_ready` dice lo mismo sin esperar. El [ejemplo de aplicación web](../examples.md#web-app) ejecuta un bot dentro del lifespan de un servidor web y publica mensajes desde una ruta web.

Un bot que solo responde interacciones por HTTP no necesita gateway: ver [Interacciones por HTTP](http-interactions.md). `await bot.open()` lo inicia de esa forma y retorna: abre el cliente HTTP, obtiene la aplicación y el usuario del bot, envía `Starting`, sincroniza los comandos, inicia las tareas en segundo plano y devuelve la aplicación. `ASGIApp` lo llama, y `close()` lo termina. Un script que solo llama a la API REST usa un [`RESTClient`](rest.md#without-a-bot).

## Mientras se ejecuta { #while-it-runs }

| Atributo | Valor |
|---|---|
| `bot.user` | el `CurrentUser` del bot, una vez conectado |
| `bot.application_id` | el id de la aplicación, una vez iniciado |
| `bot.latency` | el promedio del tiempo de ida y vuelta del heartbeat de los shards, en segundos, o `None` |
| `bot.is_running` | si `start()` u `open()` está ejecutando el bot, hasta que `close()` lo termina |
| `bot.is_ready` | si se envió `Started` desde el último `start()` |
| `bot.rest`, `bot.http` | la [API REST](rest.md), una vez iniciado |
| `bot.gateway` | los [shards del gateway](gateway.md#raw-gateway-commands), una vez conectado |
| `bot.cache` | la [caché](cache.md) |
| `bot.voice` | las [conexiones de voz](../voice/index.md) |
| `bot.auto_defer` | el retraso por defecto del [diferimiento automático](responses.md#deferring); se puede cambiar en cualquier momento |

`bot.rest`, `bot.http` y `bot.gateway` lanzan `StateError` antes de `start()`.

## Logging { #logging }

bostie escribe en el módulo estándar `logging` con estos nombres:

| Logger | Qué informa |
|---|---|
| `bostie.bot` | handlers que fallaron o no respondieron, intents que necesita un listener, interacciones sin manejar |
| `bostie.gateway` | conexiones, desconexiones, reanudaciones, contrapresión, solicitudes inválidas |
| `bostie.rest` | rate limits y reintentos, desde el cliente HTTP de Rust |
| `bostie.sync` | qué cambió cada sincronización de comandos |
| `bostie.context` | respuestas que no se pudieron aplicar, como un flag efímero en una edición |
| `bostie.events` | payloads que no encajaron con su evento, una vez por nombre de evento |
| `bostie.voice` | conexiones de voz, DAVE, sinks, receptores y reproductores |
| `bostie.bridge` | el enlace entre Python y el núcleo en Rust |

El núcleo en Rust escribe a través de los mismos loggers y lee sus niveles cuando arranca el bot: ejecuta `logging.getLogger("bostie.gateway").setLevel(logging.DEBUG)` antes de `run()` para ver el gateway en detalle.

## Apuntar bostie a otro lugar { #pointing-bostie-elsewhere }

`Bot(api_url=...)` envía las solicitudes REST a otra URL base, como un proxy de rate limits compartido por varios procesos o un [Discord falso](testing.md) en las pruebas. Si no se indica, bostie lee `BOSTIE_API_URL` cuando arranca el bot, y usa `https://discord.com/api/v10` cuando esa variable tampoco está definida. Detrás de un proxy de rate limits, pasa `http_settings=HTTPSettings(rate_limiter=False)`, para que bostie le deje las esperas al proxy. La [guía de REST](rest.md#rate-limits-retries-and-timeouts) cubre los demás ajustes HTTP.

## Varios bots en un proceso { #several-bots-in-one-process }

Cada `Bot` tiene su propia conexión con el núcleo en Rust, su propia caché y su propio cliente HTTP, así que un proceso puede ejecutar varios bots, cada uno iniciado y cerrado por su cuenta, en un mismo event loop.

## Guardar estado { #keeping-state }

Un bot es un objeto común: crea una subclase de `Bot` para darle atributos, como hace el [ejemplo del ciclo de vida](../examples.md#lifecycle) con una conexión a una base de datos abierta en un listener de `Starting` y cerrada en uno de `Stopped`. Todas las opciones de `Bot` aparecen en la [referencia de opciones de Bot](../reference/bot-options.md).
