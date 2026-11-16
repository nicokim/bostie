---
source: 12cb33d1448fc6df340b3c11d616bf17c9f5b58c
description: "Formatea texto con menciones, marcas de tiempo y escape de markdown (bostie.fmt), colores con bostie.color y URL de la CDN de avatares, íconos y emojis."
---

# Formato, colores y URL de la CDN

Tres módulos pequeños construyen el texto y las URL que muestra Discord: `bostie.fmt` para el markdown, las menciones y las marcas de tiempo, `bostie.color` para los colores y `bostie.cdn` para las URL de las imágenes.

```python
--8<-- "docs/snippets/formatting/profile_card.py"
```

## Menciones, marcas de tiempo y enlaces { #mentions-timestamps-and-links }

| Función | Devuelve | Discord muestra |
|---|---|---|
| `fmt.user(user_id)` | `<@id>` | el usuario, y lo menciona |
| `fmt.role(role_id)` | `<@&id>` | el rol, y lo menciona |
| `fmt.channel(channel_id)` | `<#id>` | un enlace al canal |
| `fmt.command(name, command_id)` | `</name:id>` | un comando en el que se puede hacer clic; `name` puede incluir un grupo y un subcomando |
| `fmt.emoji(name, emoji_id, animated=False)` | `<:name:id>` | un emoji personalizado |
| `fmt.game_profile(game_id)` | `<@$id>` | un perfil de juego |
| `fmt.timestamp(when, style=None)` | `<t:seconds:style>` | una hora en la zona horaria y el idioma de cada lector |
| `fmt.guild_navigation(kind, role_id=None)` | `<id:kind>` | un enlace a una pestaña del servidor: `"customize"`, `"browse"`, `"guide"` o `"linked-roles"`; solo `"linked-roles"` acepta un rol |
| `fmt.store(application_id, sku_id=None)` | una URL de la tienda | la página de la tienda de la aplicación, o un SKU, como una tarjeta |

`fmt.timestamp` acepta un `datetime` o segundos Unix. Sus estilos son los de Discord: `"t"` 16:20, `"T"` 16:20:30, `"d"` 20/04/2021, `"D"` 20 de abril de 2021, `"f"` 20 de abril de 2021 a las 16:20 (el valor por defecto de Discord cuando no se indica estilo), `"F"` lo mismo con el día de la semana, `"s"` 20/04/2021, 16:20, `"S"` con segundos, y `"R"` relativo, como "en 5 minutos". `user.mention`, `role.mention` y `channel.mention` en los modelos dan el mismo texto que las funciones de mención, y `bot.command_mention` busca el id de un comando por ti ([Registrar comandos](registration.md#command-mentions)).

## Texto de los usuarios { #text-from-users }

- `fmt.escape_markdown(text)` hace que el markdown se muestre tal como se escribió, sin romper los enlaces.
- `fmt.remove_markdown(text)` lo quita.
- `fmt.escape_mentions(text)` rompe `@everyone`, `@here` y las menciones de usuarios y roles para que no puedan notificar, insertando un espacio de ancho cero.
- `fmt.mentioned_users(text)`, `fmt.mentioned_roles(text)` y `fmt.mentioned_channels(text)` devuelven los ids mencionados en un texto.

Escapar es una capa; [`allowed_mentions`](responses.md#allowed-mentions) decide qué puede notificar, diga lo que diga el texto.

## Colores { #colors }

`bostie.color` tiene los colores de marca de Discord como `int`: `BLURPLE`, `GREEN`, `YELLOW`, `FUCHSIA` y `RED`. `color.rgb(red, green, blue)` acepta tres valores de 0 a 255, `color.hsv(hue, saturation, value)` tres de 0 a 1, y `color.parse("#5865F2")` lee seis dígitos hexadecimales, con `#`, `0x` o sin nada. El `color` de los embeds, el `accent_color` de los contenedores y los roles aceptan estos valores.

## URL de la CDN { #cdn-urls }

`bostie.cdn` construye las URL de las imágenes que guarda Discord. Cada función acepta los ids y el hash de la imagen de un modelo, además de `size`, una potencia de dos de 16 a 4096, y `format`, uno de `"png"`, `"jpg"`, `"jpeg"`, `"webp"` (el valor por defecto) o `"gif"`. Una imagen animada, cuyo hash empieza con `a_`, sigue animada en WebP o GIF; `"gif"` para una imagen estática, o un tamaño fuera de la lista, lanza `ValueError`. `await bot.http.read(url)` descarga una, como explica la [guía de REST](rest.md#downloading-files).

| Función | Imagen |
|---|---|
| `display_avatar(user)` | el avatar del usuario, o el avatar por defecto si no tiene uno |
| `user_avatar(user_id, avatar)`, `default_avatar(user_id)` | el avatar propio o por defecto de un usuario |
| `user_banner(user_id, banner)`, `avatar_decoration(asset)` | el banner y la decoración de avatar de un usuario |
| `member_avatar(guild_id, user_id, avatar)`, `member_banner(...)` | el avatar y el banner de un miembro propios de un servidor |
| `guild_icon`, `guild_splash`, `guild_discovery_splash`, `guild_banner` | las imágenes de un servidor, a partir de `guild_id` y del hash |
| `guild_tag_badge(guild_id, badge)` | la insignia de una etiqueta de servidor |
| `role_icon(role_id, icon)` | el ícono de un rol |
| `emoji(emoji_id, animated=False)` | un emoji personalizado |
| `sticker(sticker_id, format_type)` | un sticker como PNG, JSON de Lottie o GIF, según su tipo de formato |
| `sticker_pack_banner(banner_asset_id)` | el banner de un paquete de stickers |
| `scheduled_event_cover(event_id, image)` | la portada de un evento programado |
| `application_icon`, `application_cover`, `application_asset`, `store_page_asset` | el ícono, la portada, los recursos de Rich Presence y las imágenes de la tienda de una aplicación |
| `team_icon(team_id, icon)` | el ícono de un equipo |
| `soundboard_sound(sound_id)` | el audio de un sonido del panel de sonidos |
