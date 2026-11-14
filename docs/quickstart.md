# Quickstart

This page creates a Discord application, adds it to a server and runs a bot with one slash command. It assumes bostie is [installed](install.md).

## Create the application

1. Open the [Developer Portal](https://discord.com/developers/applications) and create an application.
2. On its **Bot** page, reset the token and copy it. Anyone with the token controls the bot, so keep it out of your code and your repository.
3. On its **Installation** page, add the `applications.commands` and `bot` scopes to the guild install, then open the install link and add the app to a server you manage.

## Write the bot

Save this as `bot.py`:

```python
--8<-- "docs/snippets/quickstart/hello_bot.py"
```

`@bot.command` turns the function into the slash command `/hello`. Its name is the function's name and its description is the first line of the docstring. `ctx` is the [`Context`](guide/responses.md) of one use of the command: who ran it, where, and the methods that answer.

## Run it

Pass the token through the `BOSTIE_TOKEN` environment variable and start the file:

```sh
export BOSTIE_TOKEN="your token"
python bot.py
```

`bot.run()` sets up logging at INFO, connects, and compares the commands you defined with those registered on Discord. The first start registers `/hello` and logs `commands synced (global): added ['hello'], ...`; later starts send nothing unless a command changed. Type `/hello` in your server and the bot answers.

Ctrl+C, or a SIGTERM from a process manager, closes the bot cleanly.

## Add an option

Parameters after `ctx` become the command's options, typed by their hints:

```python
--8<-- "docs/snippets/quickstart/roll_bot.py"
```

`sides` is an integer option from 2 to 100 that defaults to 6, so Discord shows it as optional and refuses values outside the range before the bot sees them. Restart the bot and it sends the changed command.

## Next

- [Slash commands](guide/commands.md) covers every option type, choices, autocomplete and subcommands.
- [Answering interactions](guide/responses.md) explains `respond`, deferring, follow-ups and ephemeral answers.
- [Registering commands](guide/registration.md) shows how to register commands in one test server while you work on them.
- [Running a bot](guide/running.md) covers the token, logging, the bot's lifecycle and running it inside another program.
