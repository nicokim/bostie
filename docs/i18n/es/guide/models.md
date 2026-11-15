---
source: 8bfaf791038898b7b25fb584ff4496c345a14fcb
---

# Modelos y enums

Todo lo que bostie decodifica de Discord es un modelo: `Message`, `Guild`, `Member`, `Interaction` y unos doscientos más, todos importables desde `bostie`. Los modelos son structs inmutables de `msgspec`, decodificados directamente de los bytes del JSON.

```python
--8<-- "docs/snippets/models/inspect_message.py"
```

## Los tipos siguen la documentación de Discord { #types-follow-discords-documentation }

Cada campo tiene el tipo que describe la documentación de Discord:

- Un campo que Discord siempre envía no tiene valor por defecto y nunca falta. Si la documentación dice que puede ser `null`, es `T | None`.
- Un campo que la documentación marca como opcional tiene un valor por defecto: `()` para una lista, un conjunto de flags vacío para los flags, `False` para un booleano cuya ausencia significa falso, y `None` en los demás casos.
- Los ids son `int`, las marcas de tiempo son objetos `datetime` con zona horaria en UTC, y los colores son `int`.
- Donde Discord envía un objeto parcial, el campo tiene un modelo parcial: `PartialUser`, `PartialMember`, `PartialChannel`, `PartialMessage`, `PartialEmoji`, `PartialApplication`, `PartialIntegration`, `PartialEntitlement`, `UnavailableGuild`. Un modelo parcial solo tiene los campos que tiene el payload parcial, así que un campo ausente es un error de tipos, no un `None` en tiempo de ejecución.

Tres casos en los que un mismo payload tiene dos formas tienen dos clases:

- Un mensaje enviado por un webhook, incluidas las respuestas a interacciones, es un `WebhookMessage`, cuyo `author` es un `PartialUser`. Cualquier otro mensaje es un `Message` con un autor `User` completo. Los campos que aceptan los dos tienen el tipo `Message | WebhookMessage`.
- Un miembro resuelto a partir de una opción, un menú de selección o el objetivo de un comando es un `ResolvedMember`, que trae su `user` y no tiene `deaf` ni `mute`. Un `Member` del gateway o de REST tiene los dos.
- Un usuario en `message.mentions` es un `MentionedUser`: un `User` con el `member` que Discord agrega cuando el mensaje se envió en un servidor.

Las interacciones son `CommandInteraction`, `ComponentInteraction`, `ModalSubmitInteraction` o `AutocompleteInteraction` según su tipo, cada una con `data` tipado.

## Resistencia a los cambios { #change-resilience }

Discord agrega campos, valores de enum y tipos de componente sin aviso, y un bot no debe romperse cuando eso pasa:

- Los campos que bostie no conoce se ignoran al decodificar.
- Los enums asignan un valor desconocido a un miembro estable llamado `UNKNOWN_<value>`, que es igual al valor al compararlo: `MessageType(99).name == "UNKNOWN_99"`.
- Los flags conservan los bits que bostie no conoce, con todos los operadores, `~` incluido.
- Un tipo de componente desconocido se decodifica como `UnknownComponent`, que conserva su JSON sin procesar y se vuelve a enviar sin cambios cuando lo pasas en `components`; un tipo de interacción desconocido se decodifica como `UnknownInteraction`, que llega a los listeners de `InteractionCreate` pero a ningún comando ni ruta.
- Un mensaje anidado, como `message.referenced_message` o el mensaje de una interacción de componente, se decodifica solo cuando lees la propiedad, así que uno roto falla solo donde se lee.

Un payload de evento que no encaja para nada con su clase, por ejemplo porque falta un campo obligatorio, se registra en el log una vez y se entrega solo a los listeners de `RawEvent`, como explica [Eventos](events.md#raw-events-and-events-bostie-does-not-model).

## Payloads sin procesar { #raw-payloads }

Un modelo decodificado a partir de un payload completo lo conserva: `model.raw_json` contiene los bytes y `model.raw` los decodifica en un `dict` nuevo. Los modelos anidados, como el autor de un mensaje, tienen `raw_json = None`. Todos los eventos también tienen `raw` y `raw_json`. Con ellos llegas a cualquier campo que Discord envíe antes de que bostie lo modele.

El `repr` de un modelo deja afuera `raw_json` y los secretos: `token`, `access_token`, `refresh_token` e `id_token`.

## Construir y copiar modelos { #building-and-copying-models }

Los modelos son inmutables y se construyen con argumentos por palabra clave. `msgspec.structs.replace(model, field=value)` devuelve una copia con algunos campos cambiados. Los modelos que le envías a Discord, como `Embed`, `AllowedMentions`, `PollCreateRequest`, `MessageReference`, `ForumTag`, `RecurrenceRule` y `AutoModerationAction`, dejan afuera los campos que no definiste, así que uno decodificado se vuelve a enviar tal como llegó.

## Funciones auxiliares en los modelos { #helpers-on-models }

| Función auxiliar | Valor |
|---|---|
| `user.mention`, `member.mention`, `role.mention`, `channel.mention` | el texto de la mención, como `<@123>` |
| `user.display_name`, `member.display_name` | el nombre global o el nombre de usuario; para un miembro, primero el apodo |
| `message.jump_url` | el enlace al mensaje |
| `message.author_member` | el autor como `ResolvedMember`, cuando el mensaje trae el miembro |
| `role.permissions`, `overwrite.allow`, `overwrite.deny` | flags `Permissions` |
| `member.permissions` | los permisos de un miembro en el canal de la interacción, cuando Discord los envió |
| `webhook.url` | la URL del webhook, cuando tiene un token |

La [guía de formato](formatting.md) cubre las menciones, las marcas de tiempo y las URL de la CDN construidas a partir de ids.

## Snowflakes { #snowflakes }

Los ids de Discord codifican cuándo se creó el objeto. Todo modelo con un id snowflake tiene `created_at`, ese momento como un `datetime` en UTC: `message.created_at`, `member.created_at` (el de la cuenta), `emoji.created_at` (`None` para un emoji Unicode). `bostie.snowflake_time(id)` hace lo mismo para un id suelto, y `bostie.time_snowflake(when)` devuelve el id más bajo creado en un momento, o con `high=True` el más alto, para paginar por fecha. Un `datetime` sin zona horaria se interpreta como hora local.

## Enums { #enums }

`bostie` exporta los enums y flags de Discord: `ChannelType`, `MessageType`, `Permissions`, `Intents`, `MessageFlags`, `Locale`, `ButtonStyle` y los demás de la [referencia de enums](../reference/api/enums.md). Los flags se combinan con `|` y se comprueban con `in`: `Permissions.SEND_MESSAGES in member_permissions`. `message.type.deletable` indica si Discord permite eliminar un mensaje de ese tipo: no se pueden eliminar los avisos de MD grupales y de llamadas, los cambios de nombre e ícono del canal ni el mensaje inicial de un hilo. `JSONErrorCode` nombra los códigos de error de Discord, tal como los informa `HTTPException.code`.
