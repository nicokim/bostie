---
source: 542ddb2b37fd206f8480755434912ca6bbefa224
description: "Abre modales de Discord con campos de texto, menús de selección, archivos, grupos de opciones y casillas, lee los valores y espera el envío en un handler."
---

# Modales

![Bostie completa un formulario y lo envía](../assets/mascot/bostie-modals.webp){ align=right width="180" loading=lazy }

Un modal es un formulario emergente. Un comando o un componente responde con uno, y el envío del usuario llega como una interacción nueva que maneja una ruta de modal.

```python
--8<-- "docs/snippets/modals/application_form.py"
```

`ui.modal(custom_id, title, *components)` acepta un título de 1 a 45 caracteres y de uno a cinco componentes: campos `ui.label(...)` y párrafos `ui.text(...)`. `await ctx.modal(...)` lo abre; tiene que ser la primera respuesta de la interacción, y después nada más puede responder esa interacción.

`@bot.on_modal(template)` enruta el envío igual que [`on_component`](components.md#routes) enruta los clics: las mismas plantillas con marcadores de posición `{name}` y `{name:int}`, el mismo `route.id(...)`, los mismos ajustes `ephemeral`, `auto_defer` y `checks`.

## Campos { #fields }

`ui.label(label, component, description=None)` pone una etiqueta de hasta 45 caracteres y una descripción de hasta 100 encima de una entrada:

| Entrada | Builder | Se lee con |
|---|---|---|
| un cuadro de texto | `ui.text_input(custom_id, style=TextInputStyle.SHORT)` | `ctx.text(custom_id)`, un `str` |
| un menú de selección de opciones | `ui.string_select(custom_id, options)` | `ctx.values(custom_id)` |
| un menú de selección de usuarios, roles, ambos o canales | `ui.user_select`, `ui.role_select`, `ui.mentionable_select`, `ui.channel_select` | `ctx.users`, `ctx.members`, `ctx.roles`, `ctx.channels` o `ctx.ids` |
| archivos subidos | `ui.file_upload(custom_id)` | `ctx.attachments(custom_id)`, los objetos `Attachment` subidos |
| una opción entre 2 y 10 | `ui.radio_group(custom_id, options)` | `ctx.choice(custom_id)`, el valor o `None` |
| varias opciones entre 1 y 10 | `ui.checkbox_group(custom_id, options)` | `ctx.values(custom_id)` |
| una casilla de sí o no | `ui.checkbox(custom_id, default=False)` | `ctx.checked(custom_id)`, un `bool` |

Todas las entradas salvo la casilla aceptan `required`, `True` por defecto, y `min_values=0` necesita `required=False`. `text_input` acepta `style=TextInputStyle.PARAGRAPH` para varias líneas, `placeholder`, `value` para completarlo de antemano, `min_length` (de 0 a 4000) y `max_length` (de 1 a 4000). `file_upload` acepta `min_values` y `max_values`, hasta 10 archivos, y `file_types` como `["image", ".pdf"]`. Las opciones de los grupos de botones de opción y de casillas son `ui.group_option(label, value, description=None, default=False)`; un grupo de casillas sin `max_values` permite que el usuario las marque todas.

Los builders devuelven `ui.ModalDict`, `ui.LabelDict`, `ui.TextInputDict`, `ui.FileUploadDict`, `ui.RadioGroupDict`, `ui.CheckboxGroupDict`, `ui.CheckboxDict` y `ui.GroupOptionDict`. Los métodos de lectura de `ModalContext` aceptan el custom id de la entrada. Un id que el modal no tiene lanza `KeyError`, y leer una entrada de otro tipo, como `ctx.text` en una casilla, lanza `TypeError`.

## Actualizar el mensaje que abrió el modal { #updating-the-message-that-opened-the-modal }

Un modal abierto desde un componente conoce el mensaje en el que está el componente, y su envío puede editar ese mensaje:

```python
--8<-- "docs/snippets/modals/settings_modal.py"
```

`await ctx.update(...)` y `await ctx.defer_update()` funcionan en el envío igual que en un clic. En un modal abierto desde un comando no hay ningún mensaje que actualizar, y lanzan `InteractionResponded`; responde con `respond` en su lugar. Un envío sin respuesta se difiere después de `auto_defer` segundos, con `defer_update` cuando hay un mensaje.

## Esperar el envío dentro de un handler { #waiting-for-the-submit-inside-a-handler }

```python
--8<-- "docs/snippets/modals/inline_modal.py"
```

`await bot.wait_for_modal(*custom_ids, check=None)` devuelve el `ModalContext` del siguiente envío cuyo custom id sea uno de `custom_ids`, o de cualquier modal si no se indica ninguno, y no se ejecuta ninguna ruta para él. Pon algo único en el custom id, como el id de la interacción, para que dos usuarios que ejecutan el comando a la vez reciban cada uno su propio envío. Un usuario puede cerrar un modal sin enviarlo, y Discord no le avisa nada al bot, así que ponle un límite a la espera con `asyncio.timeout()`.
