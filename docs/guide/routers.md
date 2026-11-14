# Routers and modules

A `Router` holds commands, listeners, routes, tasks, checks, hooks and error hooks, like the bot, without being one. Routers split a bot into modules, give a group of commands shared settings, and can be swapped while the bot runs. The bot is itself a router, so everything on this page works on it too.

```python
--8<-- "docs/snippets/routers/moderation_router.py"
```

A router has every decorator of the bot: `command`, `group`, `user_command`, `message_command`, `entry_point`, `listen`, `listen_raw`, `on_component`, `on_modal`, `every`, `check`, `before_invoke`, `after_invoke` and `on_error`. `bot.include(router)` adds all of it to the bot, and anything registered on the router later as well. Inside its handlers, `router.bot` is the bot it was included in; before that, it raises `StateError`.

Routers nest: `router.include(other)` works the same way, and including the outer one in the bot brings both.

## Defaults

`Router(**defaults)` sets defaults for the commands and routes registered on it:

| Default | Applies to |
|---|---|
| `ephemeral` | answers of its commands and routes |
| `auto_defer` | its commands and routes |
| `default_permissions` | its top-level commands |
| `nsfw` | its top-level commands |
| `contexts`, `integration_types` | its global commands |

A command or route uses its own setting first, then the defaults of its router, then those of each router that includes it, from the nearest out, then the bot's. In the example, `/ban` is hidden from members without Ban Members, usable only in guilds, and answered ephemerally, so only the moderator who ran it sees and can press the button.

The checks, before hooks and after hooks of a router apply to its handlers; those of the routers that include it run too, outermost first. Error hooks are looked up from the nearest router out, as [Checks, hooks and errors](checks.md#error-hooks) explains.

## Modules

`bot.include("greetings")` imports the module `greetings` and includes the `Router` it names `router`. A module without one raises `TypeError`.

`bot.exclude("greetings")` removes everything the router added, cancels its tasks, and makes Python forget the module and its submodules, so including it again imports the file anew. `bot.reload("greetings")` does both in one call: if the new import or include fails, the old router stays included and the error is raised. Reloading changes the handlers at once; call `await bot.sync_commands()` afterwards when command definitions changed, as the [modular example](../examples.md#modular-bot) does in its `/reload` command.

`exclude` and `reload` also take a `Router` object, for routers that are not a module's `router`.

## Conflicts

Two commands with the same name, two user or message commands with the same name, two routes with the same template, or one function registered twice as a task raise when the second is added, including when a router brings it. A router can be included once, and never into itself.

## Looking inside

`router.commands`, `router.context_menus`, `router.component_routes`, `router.modal_routes`, `router.tasks` and `router.listeners` hold what is registered, by name, template or event. `router.walk_commands()` yields every slash command and subcommand that has a handler. `router.match_component(custom_id)` and `router.match_modal(custom_id)` return the route a custom id reaches and its placeholder values, or `None`, and `router.find_error_handlers(error)` yields the error hooks that would handle an exception, nearest first.
