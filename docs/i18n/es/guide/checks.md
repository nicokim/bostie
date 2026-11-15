---
source: 029b6094446c707896abd078da818c4746bbebec
---

# Checks, hooks y errores

![Bostie de brazos cruzados, echando humo](../assets/mascot/bostie-angry.webp){ align=right width="180" loading=lazy }

Un check decide si un handler puede ejecutarse. Los hooks ejecutan código antes y después de los handlers. Los hooks de errores responden cuando algo lanza una excepción.

## Checks { #checks }

```python
--8<-- "docs/snippets/checks/command_checks.py"
```

Un check es una función que recibe el contexto y devuelve un `bool`, de forma síncrona o asíncrona. `False` rechaza la ejecución con `CheckFailure`, y un check puede lanzar su propio `CheckFailure`: su mensaje es lo que se le dice al usuario. Los checks van en el ajuste `checks=[...]` de un comando, grupo, subcomando, comando de usuario o de mensaje, punto de entrada, o ruta de componente o de modal, y `@router.check` registra uno para todos los handlers de un router; el bot es un router.

Primero se ejecutan los checks del bot, luego los de cada router desde el más externo hacia adentro, luego los de los grupos de arriba hacia abajo y, por último, los del propio comando. El primer rechazo detiene la ejecución. El autocompletado no ejecuta checks.

`bostie.checks` tiene los más comunes:

| Check | Rechaza con | Cuándo |
|---|---|---|
| `has_permissions(permissions)` | `MissingPermissions` | al usuario le falta alguno de los `permissions` en el canal, o está en un MD |
| `bot_has_permissions(permissions)` | `BotMissingPermissions` | al bot le falta alguno de ellos en el canal |
| `Cooldown(rate, seconds, per="user")` | `CooldownActive` | la clave ya lo usó `rate` veces en los últimos `seconds` |
| `MaxConcurrency(number, per="global")` | `MaxConcurrencyReached` | todavía hay `number` ejecuciones en curso para la clave |
| `has_role(role)` | `MissingRole` | el usuario no tiene el rol, indicado por id o por nombre, o está en un MD |
| `has_any_role(*roles)` | `MissingRole` | el usuario no tiene ninguno de los roles |
| `is_owner` | `NotOwner` | el usuario no está entre los `bot.owner_ids` |

Un objeto check que además tiene un método `release(ctx)`, un `checks.ReleasingCheck` como `MaxConcurrency`, se libera cuando termina el handler, sin importar cómo termine.

`per` es `"user"`, `"guild"` (el usuario, en los MD), `"channel"`, `"global"` o una función del contexto que devuelve cualquier clave hasheable. Los checks de permisos leen los permisos que Discord envía con la interacción, así que no necesitan caché ni solicitudes. Sus errores traen `missing`, los `Permissions` que faltan, y `CooldownActive` trae `retry_after` en segundos.

## Roles y propietarios { #roles-and-owners }

```python
--8<-- "docs/snippets/checks/staff_checks.py"
```

`has_role` y `has_any_role` aceptan ids o nombres de roles. Un id se compara con los roles que Discord envía con la interacción, así que no necesita caché. Un nombre se busca entre los roles del servidor en caché, así que un bot sin caché, o una [aplicación solo por HTTP](http-interactions.md), indica los roles por id. `MissingRole` trae los `roles` que se pidieron, y su mensaje menciona un rol indicado por id, que Discord muestra con el nombre del rol.

`is_owner` es un check tal cual, sin paréntesis. `bot.owner_ids` contiene los usuarios que deja pasar: los `owner_ids` pasados a `Bot(...)` o, si no hay, el propietario de la aplicación, que se lee de la aplicación cuando arranca el bot. En una aplicación que pertenece a un equipo, son el propietario del equipo y los administradores y desarrolladores que aceptaron la invitación; los miembros de solo lectura quedan afuera.

## Lo que se le dice al usuario { #what-the-user-is-told }

Cuando ningún hook de errores maneja un `CheckFailure`, bostie responde con su mensaje, de forma efímera: "You need the Manage Messages permission.", "The bot needs the Manage Messages permission.", "Try again" con una marca de tiempo relativa como "in 42 seconds", "This is already running; try again once it is done.", "You need the Moderator role.", "Only the bot's owners can use this.", o "You cannot use this." para un `False` simple.

## Hooks antes y después { #hooks-before-and-after }

```python
--8<-- "docs/snippets/checks/error_hooks.py"
```

`@router.before_invoke` registra un `async def hook(ctx)` que se ejecuta después de que pasaron los checks y antes del handler; `@router.after_invoke`, uno que se ejecuta después del handler, también cuando lanzó una excepción. Se ejecutan para cada comando, ruta de componente y ruta de modal del router, así que un hook que puede ver un componente no debería dar por hecho un `Context`. Los hooks previos del bot se ejecutan primero y sus hooks posteriores, al final. Una excepción de un hook posterior se registra en el log y no cambia la respuesta.

## Hooks de errores { #error-hooks }

Una excepción de un check, un conversor, un hook previo o el handler va a un hook de errores. `@x.on_error` registra un `async def hook(ctx, error)` para cualquier `Exception`, y `@x.on_error(NotFound, ValueError)` para esos tipos y sus subclases. `x` es un comando, un grupo, una ruta de componente o de modal, un router o el bot, y cada uno puede tener un hook por tipo de excepción.

bostie busca un hook en este orden, y el primero que encuentra maneja el error:

1. el comando, luego su grupo y el grupo de nivel superior;
2. el router al que pertenece el comando, luego cada router en el que está incluido, hasta llegar al bot.

Dentro de un mismo lugar, gana el hook de la clase más cercana a la del error: un hook para `NotFound` le gana a uno para `HTTPException`. Un hook que vuelve a lanzar el mismo error se lo pasa al siguiente hook en ese orden, como hace `slow_down` arriba. Un hook que lanza otro error hace que se registre en el log.

El tipo del contexto es `ErrorContext`: un `Context`, `ComponentContext`, `ModalContext` o `AutocompleteContext`. El contexto de autocompletado no puede llamar a `respond`, así que compruébalo como arriba.

Cuando un hook termina sin responder, bostie deja una advertencia en el log y responde "Something went wrong.". Cuando ningún hook maneja el error, bostie responde a un `CheckFailure` con su mensaje, y registra cualquier otra cosa en el log con su traceback antes de responder "Something went wrong.". No se responde nada cuando el handler ya había respondido, y un autocompletado recibe una lista vacía de sugerencias.

## Errores fuera de los handlers { #errors-outside-handlers }

Los listeners y las tareas no tienen un usuario al que responder. Una excepción en un listener se envía como un evento `ListenerError`, y una en una tarea como un `TaskError`; si no hay un listener para ellos, bostie los registra en el log. Ver [Eventos](events.md#when-a-listener-raises) y [Tareas en segundo plano](tasks.md).

## Tipos de excepción { #exception-types }

Todo lo que bostie lanza sobre Discord, la red o el estado del bot es un `BostieError`. Un argumento incorrecto lanza `ValueError` o `TypeError`. Algunos errores también son una excepción estándar: `StateError` es un `RuntimeError`, `MissingIntent` un `ValueError`, `StartupError` un `OSError` y `CommandDefinitionError` un `TypeError`. Los errores de comandos e interacciones comparten la base `CommandError`, y los errores de voz, `VoiceError`.

| Excepción | Se lanza cuando |
|---|---|
| `HTTPException` | una solicitud REST terminó con un código de error; `status`, `code` (un `JSONErrorCode`), `message`, `errors` y el `body` sin procesar |
| `BadRequest`, `Forbidden`, `NotFound`, `ServerError` | 400, 403, 404 y 5xx |
| `RateLimited` | 429, o bostie retuvo una solicitud que esperaría más que `max_rate_limit_wait`; `retry_after`, `is_global` |
| `InvalidToken` | Discord rechazó el token, en REST o en el gateway |
| `TransportError` | una solicitud falló antes de que Discord respondiera |
| `UnexpectedResponse` | Discord aceptó una solicitud pero su respuesta no se puede decodificar; `raw` la contiene |
| `RequestBudgetExhausted` | demasiadas solicitudes recientes fueron inválidas, y bostie rechazó una para proteger la IP |
| `GatewayClosed`, `PrivilegedIntentsRequired`, `ShardingRequired` | Discord cerró el gateway de forma definitiva; los dos últimos para los códigos 4014 y 4011 |
| `ShardNotRunning`, `GatewayQueueFull` | un comando del gateway para un shard que este proceso no ejecuta, o con demasiados comandos en espera |
| `StateError` | una llamada que el estado del objeto no permite, como `bot.rest` antes de `start()` |
| `MissingIntent` | una función necesita un intent que el bot no pidió |
| `MemberRequestRateLimited` | Discord rechazó una solicitud de miembros; `retry_after` |
| `StartupError` | bostie no pudo preparar un recurso del sistema que necesita |
| `CommandDefinitionError` | un comando, grupo u opción rompe las reglas de Discord; lo lanza el decorador |
| `CommandSyncError` | Discord rechazó los comandos durante una sincronización |
| `OptionConversionError` | una opción u objetivo no se pudo convertir a su anotación |
| `InteractionResponded`, `InteractionNotAnswered`, `InteractionExpired` | una llamada para responder que el estado de la interacción no permite |
| `CheckFailure` y sus subclases | un check rechazó una ejecución |

Los errores de voz están en la [página de voz](../voice/index.md#errors). La [referencia de errores](../reference/api/errors.md) enumera cada clase con sus atributos.
