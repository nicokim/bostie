---
source: b7efeeb83216fd56b3819a874abdb0bfb6a2a072
description: "Cómo sincroniza bostie los comandos con Discord: ámbito global o de servidor, un servidor de pruebas, menciones, traducciones y errores de sincronización."
---

# Registrar comandos

Discord muestra un comando solo después de que la aplicación lo registró. bostie lo hace por ti: cuando el bot arranca, compara los comandos que definiste con los que tiene Discord y los envía solo si algo es distinto.

## La sincronización al arrancar { #the-sync-at-startup }

`bot.start()` y `bot.run()` sincronizan antes de que se conecte el gateway. Para cada ámbito, global o un servidor, bostie obtiene los comandos registrados con sus traducciones, los compara campo por campo y, cuando son distintos, reemplaza los comandos del ámbito en una sola solicitud. El log dice `commands up to date (global)` o `commands synced (global): added [...], removed [...], changed [...]`.

Un comando que Discord tiene pero que ya no defines se elimina, salvo que su tipo sea uno que nunca defines: un bot sin `@bot.entry_point` conserva el comando de punto de entrada que Discord creó para su actividad.

`Bot(auto_sync=False)` omite la sincronización. `await bot.sync_commands()` reemplaza ya mismo los comandos de todos los ámbitos, hayan cambiado o no, y devuelve un `SyncResult` por ámbito con los nombres agregados (`added`), eliminados (`removed`) y modificados (`changed`), y los `ids` que Discord les dio a los comandos slash.

## Ámbitos { #scopes }

Un comando sin `guilds` es global: funciona en cada servidor que instaló la aplicación y, según lo permitan sus `contexts`, en los MD. `guilds=[...]` lo registra solo en esos servidores.

Mientras trabajas en un bot, registra todo en un servidor de pruebas:

```python
--8<-- "docs/snippets/commands/command_sync.py"
```

`Bot(dev_guilds=[...])` registra en esos servidores, en lugar de hacerlo de forma global, cada comando que no tiene `guilds` propios. El comando de punto de entrada siempre es global, así que la sincronización lo deja afuera y advierte.

### Un nombre en varios ámbitos { #one-name-in-several-scopes }

```python
--8<-- "docs/snippets/commands/guild_variants.py"
```

Varios comandos pueden compartir un nombre cuando ningún servidor tiene dos de ellos: un `/rules` global y un `/rules` para un servidor, o un `/rules` por servidor. Cada uno se registra en su propio ámbito, y una interacción ejecuta el comando registrado en su servidor o, si no hay, el global. En ese servidor Discord muestra tanto el comando global como el del servidor. Con `dev_guilds`, el comando propio de un servidor ocupa el lugar del global en ese servidor. Dos comandos con el mismo nombre cuyos servidores se superponen, o dos globales, lanzan `CommandDefinitionError` cuando se registra el segundo. Los comandos de usuario y de mensaje siguen las mismas reglas.

La sincronización solo toca los ámbitos que tienen comandos. Cuando quitas `dev_guilds`, los comandos se quedan en el servidor de pruebas junto a los nuevos globales; `await bot.rest.commands.bulk_overwrite_guild(bot.application_id, guild_id, [])` los borra.

## Dónde se pueden usar los comandos { #where-commands-can-be-used }

`contexts` e `integration_types` se aplican a los comandos globales. Defínelos en un comando, un router o el bot:

- `contexts`: `InteractionContextType.GUILD`, `BOT_DM` (los MD del bot) y `PRIVATE_CHANNEL` (otros MD y MD grupales, para una aplicación instalada por un usuario).
- `integration_types`: `ApplicationIntegrationType.GUILD_INSTALL` y `USER_INSTALL`.

`Bot(contexts=[...], integration_types=[...])` es el valor por defecto de cada comando global que no define el suyo. Si no se definen en ningún lado, bostie no envía ninguno de los dos campos y Discord aplica sus valores por defecto; para `integration_types`, son los contextos de instalación configurados para la aplicación. Quitar un ajuste que habías definido cuenta como un cambio, así que la siguiente sincronización le vuelve a dar al comando los valores por defecto de Discord.

## Menciones de comandos { #command-mentions }

`await bot.command_mention("roles color set")` devuelve `</roles color set:ID>`, que Discord muestra como un comando en el que se puede hacer clic. Usa los ids de la última sincronización, o se los pide a Discord una vez por ámbito. Con `guild_id=`, un comando registrado en ese servidor tiene prioridad sobre uno global con el mismo nombre. Un nombre desconocido lanza `LookupError`, y llamarlo antes de que el bot arranque lanza `StateError`. `fmt.command(name, command_id)` construye el mismo texto a partir de un id que ya tienes.

## Traducciones { #translations }

`name_localizations` y `description_localizations` en un comando, un grupo, un subcomando, una `Option` o una `Choice` traducen un texto. Una función de traducción los traduce todos:

```python
--8<-- "docs/snippets/commands/command_translations.py"
```

`Bot(translate=function)` llama a `function(text, locale)` durante la sincronización para cada nombre y cada descripción de cada comando, grupo, subcomando y opción, y para cada nombre de choice, una vez por `Locale`. Si devuelve `None`, o el texto sin cambios, no se agrega nada para ese idioma, y una traducción que escribiste tú tiene prioridad sobre la función. El handler ve el idioma del usuario en `ctx.locale` y el del servidor en `ctx.interaction.guild_locale`.

## Cuando Discord rechaza los comandos { #when-discord-refuses-the-commands }

Una sincronización que Discord rechaza con un 400 lanza `CommandSyncError`. Su mensaje tiene una línea por problema, con el comando, la ruta de la opción y el mensaje de Discord, y `error.problems` los contiene como objetos `SyncProblem`. La mayoría de los problemas se detectan antes: los decoradores comprueban nombres, descripciones y límites cuando se ejecutan.
