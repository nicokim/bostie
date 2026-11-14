# Examples

These are the files of the repository's `examples/` folder. The test suite type-checks them and loads each one, which builds its bot and checks every command definition. Each runs with `BOSTIE_TOKEN` set: `python examples/commands.py`.

## Commands

Typed options, a group, autocomplete, user and message commands, checks, an error hook, and translations from gettext catalogs.

```python
--8<-- "examples/commands.py"
```

## Components

A Components V2 tour with a pager, a select menu that recolors a container, and a kick that waits for a confirmation click.

```python
--8<-- "examples/components.py"
```

## Modals

A modal opened from a button and read on submit, and one that a command waits for.

```python
--8<-- "examples/modals.py"
```

## Events

Listeners by annotation, a union, an event of the bot's own, a `ListenerError` listener, and a guessing game built on `wait_for`.

```python
--8<-- "examples/events.py"
```

## Lifecycle

A database opened in a `Starting` listener and closed in a `Stopped` one, and two background tasks.

```python
--8<-- "examples/lifecycle.py"
```

## Music

A queue per guild with skip, loop and leave.

```python
--8<-- "examples/music.py"
```

## Recording

One Ogg Opus file per speaker until `/stop`.

```python
--8<-- "examples/recording.py"
```

## Activity

An entry point command and a slash command that open the app's Activity.

```python
--8<-- "examples/activity.py"
```

## HTTP interactions

Slash commands served over HTTP by `ASGIApp`.

```python
--8<-- "examples/http_interactions.py"
```

## REST script

The REST API without a gateway.

```python
--8<-- "examples/rest_script.py"
```

## Web app

A bot started inside a web app's lifespan, with a route that posts through the bot's REST client.

```python
--8<-- "examples/web_app.py"
```

## Webhook

Posting through a webhook URL, from async code and from plain code.

```python
--8<-- "examples/webhook.py"
```

## Linked roles

A linked roles verification page: the OAuth2 login, the code exchange, and the user's role connection.

```python
--8<-- "examples/linked_roles.py"
```

## Modular bot

A bot that includes a module by name and reloads it from a command.

```python
--8<-- "examples/modular/bot.py"
```

```python
--8<-- "examples/modular/greetings.py"
```
