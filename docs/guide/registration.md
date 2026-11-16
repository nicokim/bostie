---
description: "How bostie syncs application commands with Discord: global and guild scopes, a test guild while developing, command mentions, translations and sync errors."
---

# Registering commands

Discord shows a command only after the app has registered it. bostie does that for you: when the bot starts, it compares the commands you defined with those Discord holds and sends them only if something differs.

## The sync at startup

`bot.start()` and `bot.run()` sync before the gateway connects. For each scope, global or one guild, bostie fetches the registered commands with their localizations, compares them field by field, and replaces the scope's commands in one request when they differ. The log says `commands up to date (global)` or `commands synced (global): added [...], removed [...], changed [...]`.

A command Discord holds but you no longer define is removed, unless its type is one you never define: a bot without an `@bot.entry_point` keeps the entry point command Discord made for its Activity.

`Bot(auto_sync=False)` skips the sync. `await bot.sync_commands()` replaces the commands of every scope now, changed or not, and returns a `SyncResult` per scope with the names it `added`, `removed` and `changed`, and the `ids` Discord gave the slash commands.

## Scopes

A command without `guilds` is global: it works in every guild that installed the app and, as its `contexts` allow, in DMs. `guilds=[...]` registers it in those guilds only.

While you work on a bot, register everything in a test guild:

```python
--8<-- "docs/snippets/commands/command_sync.py"
```

`Bot(dev_guilds=[...])` registers every command that has no `guilds` of its own in those guilds instead of globally. The entry point command is always global, so the sync leaves it out and warns.

### One name in several scopes

```python
--8<-- "docs/snippets/commands/guild_variants.py"
```

Commands may share a name when no guild has two of them: a global `/rules` and a `/rules` for one guild, or a `/rules` per guild. Each is registered in its own scope, and an interaction runs the command registered in its guild, else the global one. In that guild Discord lists both the global and the guild command. Under `dev_guilds`, a guild's own command takes the place of the global one in that guild. Two commands of one name whose guilds overlap, or two global ones, raise `CommandDefinitionError` when the second is registered. User and message commands follow the same rules.

The sync only touches scopes that have commands. When you drop `dev_guilds`, the commands stay in the test guild next to the new global ones; `await bot.rest.commands.bulk_overwrite_guild(bot.application_id, guild_id, [])` clears them.

## Where commands can be used

`contexts` and `integration_types` apply to global commands. Set them on a command, a router or the bot:

- `contexts`: `InteractionContextType.GUILD`, `BOT_DM` (the bot's DMs) and `PRIVATE_CHANNEL` (other DMs and group DMs, for a user-installed app).
- `integration_types`: `ApplicationIntegrationType.GUILD_INSTALL` and `USER_INSTALL`.

`Bot(contexts=[...], integration_types=[...])` is the default for every global command that does not set its own. Left unset everywhere, bostie sends neither field and Discord applies its defaults; for `integration_types` that is the installation contexts configured for the app. Removing a setting you had set counts as a change, so the next sync gives the command Discord's defaults again.

## Command mentions

`await bot.command_mention("roles color set")` returns `</roles color set:ID>`, which Discord shows as a clickable command. It uses the ids of the last sync, or asks Discord once per scope. With `guild_id=`, a command registered in that guild wins over a global one of the same name. An unknown name raises `LookupError`, and calling it before the bot started raises `StateError`. `fmt.command(name, command_id)` builds the same text from an id you already have.

## Translations

`name_localizations` and `description_localizations` on a command, a group, a subcommand, an `Option` or a `Choice` translate one text. A translation function translates them all:

```python
--8<-- "docs/snippets/commands/command_translations.py"
```

`Bot(translate=function)` calls `function(text, locale)` during the sync for every name and description of every command, group, subcommand and option, and for every choice name, once per `Locale`. A return of `None`, or of the text unchanged, adds nothing for that locale, and a localization you wrote yourself wins over the function. The handler sees the user's language in `ctx.locale` and the guild's in `ctx.interaction.guild_locale`.

## When Discord refuses the commands

A sync that Discord rejects with 400 raises `CommandSyncError`. Its message has one line per problem, naming the command, the option path and Discord's message, and `error.problems` holds them as `SyncProblem` objects. Most problems are caught earlier: the decorators check names, descriptions and limits when they run.
