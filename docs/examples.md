# Examples

These bots live in the `examples` folder of the repository. Set `BOSTIE_TOKEN` and run one with `python examples/commands.py`.

## Commands

Slash commands with typed options, a group, autocomplete, user and message commands, and an error hook.

`examples/commands.py`:

```python
--8<-- "examples/commands.py"
```

## Components

Components V2 messages, a pager that keeps its page in the custom id, and a select menu.

`examples/components.py`:

```python
--8<-- "examples/components.py"
```

## Modals

A modal opened from a button, read when it is submitted.

`examples/modals.py`:

```python
--8<-- "examples/modals.py"
```

## Events

Listeners, `bot.wait_for`, and an event of your own sent with `bot.dispatch`.

`examples/events.py`:

```python
--8<-- "examples/events.py"
```

## Setup and background tasks

A database opened in `Starting` and closed in `Stopped`, with tasks on an interval and at times of day.

`examples/lifecycle.py`:

```python
--8<-- "examples/lifecycle.py"
```

## Modules

A bot that includes `greetings.py` by module name and reloads it with a command.

`examples/modular/bot.py`:

```python
--8<-- "examples/modular/bot.py"
```

`examples/modular/greetings.py`:

```python
--8<-- "examples/modular/greetings.py"
```

## A web app

The bot running inside an aiohttp app, whose route sends messages through `bot.rest`.

`examples/web_app.py`:

```python
--8<-- "examples/web_app.py"
```

## Music

A queue per guild with skip, loop and leave.

`examples/music.py`:

```python
--8<-- "examples/music.py"
```

## Recording

One Ogg Opus file per speaker until `/stop`. Tell people before you record them.

`examples/recording.py`:

```python
--8<-- "examples/recording.py"
```
