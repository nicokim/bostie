---
source: b30ad56d0f8b63739574acff05fa92b323c4d3d9
description: "Divide un bot de Discord en routers y módulos, da valores por defecto comunes a grupos de comandos, e incluye, excluye o recarga módulos con el bot en marcha."
---

# Routers y módulos

Un `Router` contiene comandos, listeners, rutas, tareas, checks, hooks y hooks de errores, como el bot, sin ser uno. Los routers dividen un bot en módulos, les dan ajustes compartidos a un grupo de comandos y se pueden cambiar mientras el bot se ejecuta. El propio bot es un router, así que todo lo de esta página también funciona en él.

```python
--8<-- "docs/snippets/routers/moderation_router.py"
```

Un router tiene todos los decoradores del bot: `command`, `group`, `user_command`, `message_command`, `entry_point`, `listen`, `listen_raw`, `on_component`, `on_modal`, `every`, `check`, `before_invoke`, `after_invoke` y `on_error`. `bot.include(router)` agrega todo eso al bot, y también lo que se registre en el router más adelante. Dentro de sus handlers, `router.bot` es el bot en el que se incluyó; antes de eso, lanza `StateError`.

Los routers se anidan: `router.include(other)` funciona igual, e incluir el externo en el bot trae a los dos.

## Valores por defecto { #defaults }

`Router(...)` acepta, como argumentos por palabra clave, valores por defecto para los comandos y rutas registrados en él:

| Valor por defecto | Se aplica a |
|---|---|
| `ephemeral` | las respuestas de sus comandos y rutas |
| `auto_defer` | sus comandos y rutas |
| `default_permissions` | sus comandos de nivel superior |
| `nsfw` | sus comandos de nivel superior |
| `contexts`, `integration_types` | sus comandos globales |

Un comando o una ruta usa primero su propio ajuste, luego los valores por defecto de su router, luego los de cada router que lo incluye, del más cercano hacia afuera, y por último los del bot. En el ejemplo, `/ban` queda oculto para los miembros sin el permiso Banear miembros, solo se puede usar en servidores y se responde de forma efímera, así que solo el moderador que lo ejecutó ve el botón y puede presionarlo.

Los checks, los hooks previos y los hooks posteriores de un router se aplican a sus handlers; los de los routers que lo incluyen también se ejecutan, empezando por el más externo. Los hooks de errores se buscan desde el router más cercano hacia afuera, como explica [Checks, hooks y errores](checks.md#error-hooks).

## Módulos { #modules }

`bot.include("greetings")` importa el módulo `greetings` e incluye el `Router` que llama `router`. Un módulo que no lo tiene lanza `TypeError`.

`bot.exclude("greetings")` quita todo lo que agregó el router, cancela sus tareas y hace que Python olvide el módulo y sus submódulos, así que volver a incluirlo importa el archivo de nuevo. `bot.reload("greetings")` hace las dos cosas en una llamada: si la nueva importación o inclusión falla, el router anterior sigue incluido y se lanza el error. Recargar cambia los handlers de inmediato; llama a `await bot.sync_commands()` después cuando cambiaron las definiciones de los comandos, como hace el [ejemplo modular](../examples.md#modular-bot) en su comando `/reload`.

`exclude` y `reload` también aceptan un objeto `Router`, para los routers que no son el `router` de un módulo.

## Conflictos { #conflicts }

Dos comandos con el mismo nombre, dos comandos de usuario o de mensaje con el mismo nombre, dos rutas con la misma plantilla, o una función registrada dos veces como tarea lanzan una excepción cuando se agrega el segundo, también cuando lo trae un router. Un router se puede incluir una sola vez, y nunca dentro de sí mismo.

## Mirar adentro { #looking-inside }

`router.commands`, `router.context_menus`, `router.component_routes`, `router.modal_routes`, `router.tasks` y `router.listeners` contienen lo que está registrado: los comandos por nombre y servidores, como `("rules", ())` para uno global; los comandos de usuario y de mensaje por tipo, nombre y servidores; las rutas por plantilla; las tareas por función y los listeners por evento. `router.walk_commands()` produce cada comando slash y cada subcomando que tiene un handler. `router.match_component(custom_id)` y `router.match_modal(custom_id)` devuelven la ruta a la que llega un custom id y los valores de sus marcadores de posición, o `None`, y `router.find_error_handlers(error)` produce los hooks de errores que manejarían una excepción, empezando por el más cercano.
