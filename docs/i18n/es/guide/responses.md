---
source: cbf9d472ef0e0c0f610b3352754ad6482b93f093
---

# Responder a interacciones

Cada comando slash, comando de menú contextual, botón, menú de selección y envío de modal es una interacción, y Discord espera que la aplicación la responda. Un handler de comando recibe un `Context`, un handler de componente un `ComponentContext` y un handler de modal un `ModalContext`; todos comparten los métodos para responder que se describen abajo.

## Lo que sabe un contexto { #what-a-context-knows }

| Atributo | Valor |
|---|---|
| `ctx.user` | el `User` que la activó, en un servidor o en un MD |
| `ctx.member` | su `Member` en un servidor; si no, `None` |
| `ctx.guild_id`, `ctx.channel_id` | dónde ocurrió; `guild_id` es `None` en los MD |
| `ctx.channel` | el `PartialChannel` que trae la interacción |
| `ctx.locale` | el idioma del cliente del usuario |
| `ctx.interaction` | el modelo completo de la interacción, para todo lo demás, como `app_permissions` o `guild_locale` |
| `ctx.bot` | el bot |
| `ctx.voice` | la [voz](../voice/index.md) para este servidor y este usuario |
| `ctx.command_name` | en `Context`, el comando con sus subcomandos, como `"roles color set"` |

## Responder, editar, dar seguimiento { #answer-edit-follow-up }

```python
--8<-- "docs/snippets/responses/answer_flow.py"
```

`ctx.respond(...)` envía la primera respuesta. Si se vuelve a llamar, envía un mensaje de seguimiento, así que un handler puede llamarlo tantas veces como necesite. Devuelve el mensaje que creó Discord, un `WebhookMessage`.

- `ctx.edit(**parts)` edita la primera respuesta, y `ctx.edit(message_id, **parts)`, un mensaje de seguimiento.
- `ctx.followup(...)` siempre envía un mensaje nuevo, salvo justo después de `defer()`, donde completa la respuesta diferida.
- `ctx.fetch()` y `ctx.fetch(message_id)` devuelven la respuesta o un mensaje de seguimiento tal como los tiene Discord ahora.
- `ctx.delete()` y `ctx.delete(message_id)` los eliminan.

`respond` y `followup` aceptan `content` y las partes que se enumeran en [Contenido de los mensajes](messages.md): `embeds`, `components`, `files`, `allowed_mentions`, `flags`, `tts`, `poll`, además de `ephemeral`. Una edición acepta las mismas partes por palabra clave: una parte que no pasas queda como está, y `None` la borra.

## Mensajes que se eliminan solos { #messages-that-delete-themselves }

```python
--8<-- "docs/snippets/responses/short_lived.py"
```

`delete_after=` en `respond`, `followup` y `bot.rest.messages.create` elimina el mensaje que envió esa cantidad de segundos después. El bot ejecuta la eliminación en segundo plano y la cancela cuando se cierra, así que un mensaje cuyo plazo vence después de que el bot se detiene se queda. Los mensajes de una interacción se eliminan a través de su token, lo que también funciona con los efímeros, pero deja de funcionar 15 minutos después de la interacción; una eliminación que vence más tarde falla y deja una advertencia en el log. Un mensaje que alguien ya eliminó se omite sin avisar.

## Respuestas efímeras { #ephemeral-answers }

`ephemeral=True` en una llamada hace que ese mensaje solo lo vea el usuario. `ephemeral=True` en un comando, una ruta de componente, una ruta de modal o un router lo vuelve el valor por defecto para sus respuestas, y una llamada todavía puede pasar `ephemeral=False`. Un mensaje existente no puede pasar de efímero a público ni al revés: una edición que lo pide deja una advertencia en el log una sola vez y mantiene el mensaje como está.

## Diferir { #deferring }

La primera respuesta tiene que llegar a Discord en 3 segundos. `await ctx.defer()` muestra en su lugar que el bot «está pensando…», y el handler tiene entonces 15 minutos para llamar a `respond`; `defer(ephemeral=True)` hace que la respuesta que viene sea efímera.

```python
--8<-- "docs/snippets/responses/deferring.py"
```

bostie también difiere por su cuenta: un uso que sigue sin respuesta `auto_defer` segundos después de llegar se difiere, 2.5 segundos por defecto. Un componente o un modal abierto desde un mensaje se difiere con `defer_update`, que mantiene el mensaje como está hasta que `update` lo edita. `auto_defer` se define en el bot, un router, un comando o una ruta, y `None` lo desactiva ahí. El temporizador cuenta desde que el gateway entregó la interacción, así que un event loop bloqueado retrasa el handler pero no el plazo; bostie deja una advertencia en el log cuando un handler empieza con más de un segundo de retraso.

## Modales y actividades { #modals-and-activities }

`await ctx.modal(ui.modal(...))` responde con un formulario emergente; ver [Modales](modals.md). `await ctx.launch_activity()` abre la actividad de la aplicación y devuelve el id de su instancia. Los dos tienen que ser la primera respuesta, y nada puede responder la interacción después de un modal: el envío del modal es una interacción nueva.

## Las reglas, y los errores por romperlas { #the-rules-and-the-errors-for-breaking-them }

Cada llamada comprueba primero el estado de la interacción:

| Llamada | Antes de cualquier respuesta | Después de `defer()` | Después de `defer_update()` | Después de una respuesta |
|---|---|---|---|---|
| `respond` | responde | completa la respuesta | envía un mensaje de seguimiento | envía un mensaje de seguimiento |
| `defer` | difiere | nada | nada | `InteractionResponded` |
| `edit`, `delete` | `InteractionNotAnswered` | edita o elimina la respuesta | edita o elimina el mensaje | edita o elimina la respuesta |
| `followup` | `InteractionNotAnswered` | completa la respuesta | envía un mensaje de seguimiento | envía un mensaje de seguimiento |
| `update` | edita el mensaje | `InteractionResponded` | edita el mensaje | `InteractionResponded` |
| `modal`, `launch_activity` | responde | `InteractionResponded` | `InteractionResponded` | `InteractionResponded` |

`update` y `defer_update` solo existen en los contextos de componentes y de modales. Cada llamada que se hace después de un modal lanza `InteractionResponded`.

El token de una interacción dura 15 minutos. Cualquier llamada después de ese plazo, o después de que Discord dijo que el token no es válido, lanza `InteractionExpired` sin enviar nada.

Un handler que termina sin responder le deja al usuario el aviso de que la aplicación no respondió, y bostie deja en el log una advertencia con el nombre del handler. Uno que difirió y nunca respondió deja el «está pensando…» hasta que vence el token, también con una advertencia. Cuando un handler lanza una excepción antes de responder, bostie responde "Something went wrong.", de forma efímera salvo que el handler haya diferido, a menos que responda un [hook de errores](checks.md#error-hooks).

Discord permite 5 mensajes de seguimiento para una interacción de una aplicación instalada solo en la cuenta del usuario, y bostie advierte en el sexto.

## Menciones permitidas { #allowed-mentions }

Por defecto, una respuesta puede mencionar usuarios y roles, pero no `@everyone` ni `@here`: `Bot(allowed_mentions=...)` es `AllowedMentions(parse=("users", "roles"))`. Un `allowed_mentions` pasado con un mensaje cambia solo los tipos de mención que define, así que `AllowedMentions(users=(123,))` limita las menciones de usuarios y deja como está el ajuste de los roles. `AllowedMentions(parse=())` silencia todas las menciones, y `replied_user=False` evita que una respuesta mencione al autor del mensaje al que responde.
