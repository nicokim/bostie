---
description: "Add user and message context menu commands and the entry point command that launches an Activity, with typed targets and the settings each one takes."
---

# User, message and entry point commands

Besides slash commands, Discord has commands that run from the context menu of a user or a message, and the entry point command that opens an app's Activity.

## User and message commands

```python
--8<-- "docs/snippets/commands/context_menus.py"
```

`@bot.user_command(name)` and `@bot.message_command(name)` register a function that takes the context and the target. The name is what the menu shows: 1 to 32 characters, and unlike slash command names it may hold uppercase letters and spaces. These commands have no description and no options.

The target's type hint says what the handler gets:

| Command | Hint | The handler receives |
|---|---|---|
| user | `User` | the user |
| user | `ResolvedMember` | the member, with its `user`; only in guilds |
| user | `User \| ResolvedMember` | the member in a guild, the user elsewhere |
| message | `Message` | a message a user or bot sent |
| message | `WebhookMessage` | a message a webhook sent, interaction answers included |
| message | `Message \| WebhookMessage` | either |

A target that does not resolve to the hinted type raises `OptionConversionError`, which goes to the [error hooks](checks.md#error-hooks). Hint a message target as `Message | WebhookMessage` unless the command only makes sense for one of them.

They take the settings of a slash command except `description`, `description_localizations` and the option settings: `name_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `guilds`, `contexts`, `integration_types`, `checks` and `extra`. Discord allows 15 user and 15 message commands per scope, and the sync warns above that.

## The entry point command

An app with Activities has one entry point command, which the App Launcher shows. `@bot.entry_point(name)` registers it with a function that takes only the context:

```python
--8<-- "docs/snippets/commands/entry_point.py"
```

The handler answers like any command: `ctx.launch_activity()` opens the Activity and returns the id of its instance, and `ctx.respond(...)` answers with a message instead. Discord refuses the command from an app without Activities enabled in the Developer Portal.

The entry point is always global. With `dev_guilds` set, the sync leaves it out and warns. When Discord already has its default entry point under another name, the sync keeps that command's id, so Discord replaces it instead of keeping both. `launch_activity()` exists on component and modal contexts too, but it has to be the first answer.

`entry_point` takes `description`, `name_localizations`, `description_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts`, `integration_types`, `checks` and `extra`. [Activities, monetization and the Social SDK](platform.md#activities) covers the rest of the Activity side.
