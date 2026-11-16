---
source: d624adfef5ab8dd6ffcb780c8ed7ca6459e83b3f
description: "Crea botones, menús de selección y diseños de Components V2 con bostie.ui, y dirige custom ids a handlers que siguen funcionando después de reiniciar el bot."
---

# Componentes

![Bostie presiona un botón grande](../assets/mascot/bostie-components.webp){ align=right width="180" loading=lazy }

Los componentes son los botones, menús de selección y bloques de diseño de un mensaje. `bostie.ui` los construye como diccionarios simples con la forma del JSON de Discord y comprueba los límites de Discord mientras los construye. Un handler registrado con `@bot.on_component` responde a sus usos.

`bostie.ui` es la forma de construir componentes. Los modelos de componente, como `bostie.Button` o `bostie.ActionRow`, son lo que contiene el campo `components` de un mensaje decodificado; `components=` también los acepta, así que los componentes de un mensaje se pueden volver a enviar, editados con `msgspec.structs.replace`, sin reconstruirlos, pero esos modelos no comprueban los límites de Discord.

## Diseño { #layout }

```python
--8<-- "docs/snippets/components/layout.py"
```

Estos son Components V2: los componentes forman el mensaje completo, con `ui.text` en lugar de `content`, como explica [Contenido de los mensajes](messages.md#components-v2).

| Builder | Qué muestra |
|---|---|
| `ui.text(markdown)` | texto, con el markdown de Discord |
| `ui.section(*texts, accessory=...)` | de uno a tres textos con un botón o una miniatura al lado |
| `ui.thumbnail(url, description=None, spoiler=False)` | una imagen pequeña, solo como accesorio de una sección |
| `ui.media_gallery(*items)` | de una a diez imágenes o videos, cada uno un `ui.gallery_item(url, description=None, spoiler=False)` |
| `ui.file(filename, spoiler=False)` | un archivo subido con el mensaje con ese nombre |
| `ui.separator(divider=True, spacing=1)` | espacio entre componentes, `spacing=2` para uno grande, con una línea salvo que `divider=False` |
| `ui.container(*components, accent_color=None, spoiler=False)` | un recuadro alrededor de filas, textos, secciones, galerías, archivos y separadores, con un borde de color |
| `ui.row(*components)` | hasta cinco botones, o un menú de selección |

Una URL puede ser `attachment://name` para mostrar un archivo subido con el mensaje. Todos los builders aceptan `id=` para el id numérico opcional del componente, y `ui.raw(dict)` pasa sin cambios un componente para el que `ui` todavía no tiene builder. Demasiados elementos, una etiqueta demasiado larga o un menú de selección dentro de una fila con botones lanzan `ValueError` al construirlos. Cada builder devuelve un diccionario tipado, como `ui.ButtonDict`, `ui.RowDict`, `ui.TextDict`, `ui.SectionDict`, `ui.ThumbnailDict`, `ui.MediaGalleryDict`, `ui.GalleryItemDict`, `ui.FileDict`, `ui.SeparatorDict`, `ui.ContainerDict`, `ui.StringSelectDict`, `ui.SelectOptionDict`, `ui.EntitySelectDict` y `ui.SelectDefaultDict`, y `ui.raw` devuelve un `ui.RawComponent`; anota tus propias funciones auxiliares con ellos, como hace `ballot` más abajo.

## Botones { #buttons }

- `ui.button(label, custom_id=..., style=ButtonStyle.SECONDARY)` le envía `custom_id` al bot cuando se hace clic. `style` es `PRIMARY`, `SECONDARY`, `SUCCESS` o `DANGER`.
- `ui.link_button(label, url)` abre una URL y nunca llega al bot.
- `ui.premium_button(sku_id)` ofrece a la venta un SKU de la aplicación y nunca llega al bot.

Los botones aceptan `emoji=`: un emoji Unicode, un emoji personalizado escrito como lo muestra Discord (`<:name:id>`, o `<a:name:id>` si es animado, como lo genera `fmt.emoji`), o un modelo `Emoji` o `PartialEmoji`. `PartialEmoji.parse(text)` lee una cadena así. Los botones también aceptan `disabled=True`. Una etiqueta tiene como máximo 80 caracteres y un custom id, de 1 a 100.

## Rutas { #routes }

Una ruta es un handler para cada custom id que encaja con una plantilla:

```python
--8<-- "docs/snippets/components/routes.py"
```

`"vote:{side:int}:{topic}"` coincide con `vote:0:Lunch at noon`. `{name}` captura uno o más caracteres sin salto de línea, y `{name:int}` un entero, incluidos los negativos. Los valores llegan al handler como argumentos por palabra clave, convertidos en el caso de `int`. Las plantillas necesitan texto literal entre dos marcadores de posición, y una plantilla sin marcadores de posición coincide exactamente con su custom id.

`vote.id(side=0, topic=topic)` construye el custom id a partir de la propia ruta, así que la plantilla se escribe una sola vez. Lanza `ValueError` si el id superaría los 100 caracteres de Discord o si al analizarlo no devolvería los mismos valores.

El estado vive en el custom id, no en un objeto de Python, así que un botón enviado la semana pasada sigue funcionando después de un reinicio, siempre que su ruta esté registrada. `@bot.on_component` acepta `ephemeral`, `auto_defer` y `checks`, como un comando, y llamar a la ruta, como en `await vote(ctx, side=0, topic="x")`, llama al handler.

Un uso de componente que no coincide con ninguna ruta no recibe respuesta: Discord muestra que la interacción falló y bostie deja una advertencia en el log, salvo que escuches `InteractionCreate`.

## Responder a un componente { #answering-a-component }

Un `ComponentContext` tiene los métodos para responder del contexto de un comando, y además:

- `await ctx.update(**parts)` edita el mensaje en el que está el componente. Las partes que no pasas se quedan, y `None` borra una.
- `await ctx.defer_update()` confirma el uso sin una respuesta visible; `update` puede editar el mensaje dentro de los 15 minutos.
- `await ctx.modal(ui.modal(...))` abre un [modal](modals.md).

`respond` envía un mensaje nuevo, como en un comando. Un uso que queda sin respuesta durante `auto_defer` segundos se difiere con `defer_update`.

## Menús de selección { #select-menus }

```python
--8<-- "docs/snippets/components/selects.py"
```

| Builder | El usuario elige | Lee lo elegido con |
|---|---|---|
| `ui.string_select(custom_id, options)` | opciones construidas con `ui.select_option(label, value)` | `ctx.values`, los valores |
| `ui.user_select(custom_id)` | usuarios | `ctx.users`, y `ctx.members` en un servidor |
| `ui.role_select(custom_id)` | roles | `ctx.roles` |
| `ui.mentionable_select(custom_id)` | usuarios y roles | `ctx.users`, `ctx.members`, `ctx.roles` |
| `ui.channel_select(custom_id, channel_types=...)` | canales, solo de los tipos indicados | `ctx.channels` |

`ctx.ids` contiene los ids elegidos en cualquier menú de selección de entidades. Leer el tipo equivocado, como `ctx.values` en un menú de selección de usuarios, lanza `TypeError`.

Todos los menús de selección aceptan `placeholder` (hasta 150 caracteres), `min_values` y `max_values` (1 por defecto, como máximo 25) y `disabled`. Un menú de selección de cadenas tiene de 1 a 25 opciones; `select_option` acepta `description`, `emoji` como los botones, y `default=True` para mostrarla como elegida. Los menús de selección de entidades aceptan `defaults`, los ids que se muestran como elegidos, y el menú de selección de mencionables acepta `default_users` y `default_roles`. `required` solo importa en un [modal](modals.md).

## Páginas { #pages }

`ui.pager(route, page, count)` devuelve una fila de tres botones para una ruta con un marcador de posición `{page:int}`: anterior, una etiqueta deshabilitada `page / count` y siguiente. La primera página es la 0, y los argumentos por palabra clave adicionales completan los otros marcadores de posición de la ruta. Como el número de página está en el custom id, la paginación sobrevive a los reinicios. El comando `/tour` del [ejemplo de componentes](../examples.md#components) lo usa.

## Esperar un clic dentro de un handler { #waiting-for-a-click-inside-a-handler }

Un comando puede esperar un uso de un componente sin una ruta:

```python
--8<-- "docs/snippets/components/confirm.py"
```

`await bot.wait_for_component(*custom_ids, check=None)` devuelve el `ComponentContext` del siguiente uso cuyo custom id sea uno de `custom_ids`, o de cualquier componente si no se indica ninguno, y para el que `check` devuelva verdadero. Para un uso que devuelve, no se ejecuta ninguna ruta. Espera para siempre, así que ponle un límite con `asyncio.timeout()`; una excepción lanzada por `check` se lanza aquí. Acepta `ephemeral` y `auto_defer` para el contexto que devuelve.
