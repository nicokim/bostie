# Examples

These bots live in the `examples` folder of the repository. Set `BOSTIE_TOKEN` and run one with `python examples/commands.py`.

## Commands

Slash commands with typed options, a group, autocomplete, user and message commands, a profile card built with `cdn` and `fmt`, and an error hook. A check makes sure the bot may manage roles, a cooldown allows one high five every 30 s, and `Bot(translate=...)` fills the localizations from gettext catalogs.

`examples/commands.py`:

```python
--8<-- "examples/commands.py"
```

## Components

Components V2 messages, a `ui.pager` that keeps its page in the custom id, a select menu, and a kick that waits for a confirming click with `bot.wait_for_component`.

`examples/components.py`:

```python
--8<-- "examples/components.py"
```

## Modals

A modal opened from a button, read when it is submitted, and one a command opens and waits for with `bot.wait_for_modal`.

`examples/modals.py`:

```python
--8<-- "examples/modals.py"
```

## An Activity

The entry point command that opens the app's Activity from the App Launcher, and a slash command that opens it too.

`examples/activity.py`:

```python
--8<-- "examples/activity.py"
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

A bot that includes `greetings.py` by module name and reloads it with a command. The router there keeps its commands to servers and answers them ephemerally.

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

## Interactions over HTTP

Slash commands served by an ASGI server instead of the gateway.

`examples/http_interactions.py`:

```python
--8<-- "examples/http_interactions.py"
```

## REST without a gateway

A script that reads the bot's guilds through a `RESTClient`.

`examples/rest_script.py`:

```python
--8<-- "examples/rest_script.py"
```

## Webhooks

Posting through a webhook URL with `WebhookClient` and `SyncWebhookClient`.

`examples/webhook.py`:

```python
--8<-- "examples/webhook.py"
```

## Linked roles

An OAuth2 login whose `BearerClient` sets the user's role connection.

`examples/linked_roles.py`:

```python
--8<-- "examples/linked_roles.py"
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
