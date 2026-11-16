---
source: 16b527ff6747318f04713be223db608d76440776
description: "Arma mensajes de Discord con embeds, archivos, encuestas, respuestas, reenvíos, Components V2, indicador de escritura y mensajes de voz, con las mismas piezas."
---

# Contenido de los mensajes

Un mensaje se construye con las mismas partes dondequiera que lo envíes: `ctx.respond`, `ctx.followup`, `ctx.edit` y `ctx.update`, `bot.rest.messages.create` y `edit`, y el `execute` de un webhook. Cada parte es un argumento por palabra clave, y una parte que no pasas no se envía.

| Parte | Tipo | Notas |
|---|---|---|
| `content` | `str` | el primer argumento posicional de `respond`, `followup`, `messages.create` y `execute` |
| `embeds` | objetos `Embed` | hasta 10 embeds enriquecidos |
| `components` | builders de `ui` | botones, menús de selección y los componentes de diseño; ver [Componentes](components.md) |
| `files` | objetos `File` | archivos subidos; ver más abajo |
| `allowed_mentions` | `AllowedMentions` | qué menciones pueden notificar; ver [Menciones permitidas](responses.md#allowed-mentions) |
| `flags` | `MessageFlags` | como `SUPPRESS_EMBEDS` y `SUPPRESS_NOTIFICATIONS` |
| `tts` | `bool` | lee el contenido en voz alta a quien tenga el canal abierto |
| `poll` | `PollCreateRequest` | una encuesta; ver más abajo |
| `ephemeral` | `bool` | solo en respuestas a interacciones |

`bot.rest.messages.create` también acepta `reply_to`, `message_reference`, `sticker_ids`, `nonce`, `enforce_nonce` y `shared_client_theme`, y `delete_after`, igual que las [respuestas a interacciones](responses.md#messages-that-delete-themselves).

## Embeds y archivos { #embeds-and-files }

```python
--8<-- "docs/snippets/responses/rich_messages.py"
```

`Embed` y sus partes (`EmbedField`, `EmbedFooter`, `EmbedAuthor`, `EmbedMedia`) son modelos inmutables con argumentos por palabra clave; un `dict` simple con la forma del JSON de Discord también funciona. `color` acepta un `int` como `0x5865F2`, y las funciones auxiliares de [`bostie.color`](formatting.md#colors) construyen uno.

`File(name, data)` sube `data` con el nombre `name`. `description` es su texto alternativo, `spoiler=True` lo difumina y `title` define el título que muestra Discord. Un embed o un componente hace referencia a un archivo subido como `attachment://name`, como hace el gráfico de arriba. bostie no comprueba los tamaños: el límite de subida de Discord depende de las mejoras del servidor y del Nitro del usuario.

## Encuestas { #polls }

`PollCreateRequest` acepta una `question` y `answers`, ambas `PollMedia` con `text` y, en las respuestas, un `emoji` opcional. `duration` está en horas, 24 si no se indica, y `allow_multiselect=True` permite que un usuario elija varias respuestas. `bot.rest.polls.end(channel_id, message_id)` cierra una encuesta antes de tiempo, `bot.rest.polls.list_answer_voters(...)` enumera quién eligió una respuesta, y los [eventos](events.md) `PollVoteAdd` y `PollVoteRemove` informan los votos a medida que ocurren.

## Edición y archivos adjuntos { #editing-and-attachments }

Una edición acepta las partes por palabra clave, además de `attachments`. Una parte que no pasas se queda, y `None` la borra: `ctx.edit(embeds=None)` quita los embeds y conserva el contenido. Cuando una edición pasa `files` o `attachments`, el mensaje conserva solo los archivos adjuntos que aparecen en `attachments`, dados como objetos `Attachment` o ids, además de los archivos nuevos.

## Components V2 { #components-v2 }

Un mensaje cuyos `components` incluyen un componente de diseño (`ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file`, `ui.separator` o `ui.thumbnail`) es un mensaje de Components V2: bostie activa el flag `IS_COMPONENTS_V2` y los componentes son el mensaje completo. Un mensaje así no puede tener `content`, `embeds`, stickers ni una encuesta, y bostie lanza `ValueError` cuando los pasas; pon el texto en `ui.text`. Los botones y menús de selección solos dentro de `ui.row` también funcionan en los mensajes clásicos con contenido.

## Respuestas { #replies }

```python
--8<-- "docs/snippets/responses/replies.py"
```

`reply_to` acepta el id de un mensaje del mismo canal. Para reenviar un mensaje, pasa en su lugar `message_reference=MessageReference(type=MessageReferenceType.FORWARD, message_id=..., channel_id=...)`; los dos no se pueden combinar. Leer `message.content` en servidores requiere el intent privilegiado Message Content, como explica la [guía del gateway](gateway.md#intents).

## Indicador de escritura { #typing-indicator }

```python
--8<-- "docs/snippets/responses/typing_indicator.py"
```

`async with ctx.typing():` muestra que el bot está escribiendo en el canal de la interacción mientras se ejecuta el bloque. bostie envía el indicador cuando empieza el bloque y de nuevo cada 9 segundos, porque Discord lo muestra durante 10, y se detiene cuando termina el bloque, también si termina por una excepción. `await ctx.typing()` lo muestra una vez. Fuera de una interacción, `bot.rest.channels.trigger_typing_indicator(channel_id)` hace lo mismo, con `await` o en `async with`. Un mensaje que el bot envía en el canal quita el indicador de inmediato.

## Mensajes de voz { #voice-messages }

```python
--8<-- "docs/snippets/responses/voice_memo.py"
```

`await voice.message(audio)` lee una ruta de archivo o bytes, mide la duración, calcula la forma de onda que dibuja Discord y devuelve un `File` listo para enviar. La extensión del nombre indica el formato; los clientes de Discord graban Ogg Opus mono a 48 kHz. Un mensaje de voz es ese único archivo y nada más: sin contenido, embeds, componentes, encuesta ni stickers. bostie activa el flag `IS_VOICE_MESSAGE` y lanza `ValueError` si hay cualquier otra cosa en el mensaje. Un `File` que construyes tú se convierte en mensaje de voz cuando tiene `duration_secs` y `waveform`, las muestras de 8 bits que dibuja Discord.
