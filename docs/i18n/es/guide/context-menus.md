---
source: db591175a7dec7d094226c92a4855de730089f1d
description: "Agrega comandos de menú contextual de usuario y de mensaje y el comando de punto de entrada que inicia una actividad, con objetivos tipados y sus ajustes."
---

# Comandos de usuario, de mensaje y de punto de entrada

Además de los comandos slash, Discord tiene comandos que se ejecutan desde el menú contextual de un usuario o de un mensaje, y el comando de punto de entrada que abre la actividad de una aplicación.

## Comandos de usuario y de mensaje { #user-and-message-commands }

```python
--8<-- "docs/snippets/commands/context_menus.py"
```

`@bot.user_command(name)` y `@bot.message_command(name)` registran una función que recibe el contexto y el objetivo. El nombre es lo que muestra el menú: de 1 a 32 caracteres, y a diferencia de los nombres de los comandos slash, puede tener mayúsculas y espacios. Estos comandos no tienen descripción ni opciones.

La anotación de tipo del objetivo indica qué recibe el handler:

| Comando | Anotación | El handler recibe |
|---|---|---|
| usuario | `User` | el usuario |
| usuario | `ResolvedMember` | el miembro, con su `user`; solo en servidores |
| usuario | `User \| ResolvedMember` | el miembro en un servidor, el usuario en cualquier otro lugar |
| mensaje | `Message` | un mensaje que envió un usuario o un bot |
| mensaje | `WebhookMessage` | un mensaje que envió un webhook, incluidas las respuestas a interacciones |
| mensaje | `Message \| WebhookMessage` | cualquiera de los dos |

Un objetivo que no se resuelve al tipo de la anotación lanza `OptionConversionError`, que va a los [hooks de errores](checks.md#error-hooks). Anota el objetivo de un mensaje como `Message | WebhookMessage`, salvo que el comando solo tenga sentido para uno de los dos.

Aceptan los ajustes de un comando slash salvo `description`, `description_localizations` y los ajustes de las opciones: `name_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `guilds`, `contexts`, `integration_types`, `checks` y `extra`. Discord permite 15 comandos de usuario y 15 de mensaje por ámbito, y la sincronización advierte si se superan.

## El comando de punto de entrada { #the-entry-point-command }

Una aplicación con actividades tiene un comando de punto de entrada, que muestra el App Launcher. `@bot.entry_point(name)` lo registra con una función que recibe solo el contexto:

```python
--8<-- "docs/snippets/commands/entry_point.py"
```

El handler responde como cualquier comando: `ctx.launch_activity()` abre la actividad y devuelve el id de su instancia, y `ctx.respond(...)` responde con un mensaje en su lugar. Discord rechaza el comando si la aplicación no tiene las actividades habilitadas en el Developer Portal.

El punto de entrada siempre es global. Con `dev_guilds` definido, la sincronización lo deja afuera y advierte. Cuando Discord ya tiene su punto de entrada por defecto con otro nombre, la sincronización conserva el id de ese comando, así Discord lo reemplaza en lugar de quedarse con los dos. `launch_activity()` también existe en los contextos de componentes y de modales, pero tiene que ser la primera respuesta.

`entry_point` acepta `description`, `name_localizations`, `description_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts`, `integration_types`, `checks` y `extra`. [Actividades, monetización y el Social SDK](platform.md#activities) cubre el resto del lado de las actividades.
