# HTTP interactions and webhook events

Discord can send interactions to a URL of your app instead of the gateway, and it sends some events, the webhook events, only to a URL. `ASGIApp` serves both from the same bot, its commands, routes and listeners unchanged.

```python
--8<-- "docs/snippets/http/http_bot.py"
```

`bostie.ASGIApp(bot)` is an ASGI application: serve `app` with any ASGI server, behind HTTPS. Then, in the Developer Portal, set the app's **Interactions Endpoint URL** and, for webhook events, its **Webhook Events URL**, with the event types you want, to where it listens. Discord checks an endpoint when you save it, with a PING and a request with a wrong signature, and `ASGIApp` answers both as Discord expects.

## What it does

When the server starts, through the ASGI lifespan, the app calls `bot.open()` unless the bot already runs on the gateway, which:

1. opens the bot's HTTP client;
2. fetches the application, for its id and the public key that signs Discord's requests, and the bot's user;
3. sends `Starting`, syncs the commands, and starts the [background tasks](tasks.md).

A failure there stops the server's startup with the error. When the server shuts down, the app closes the bot if it started it.

Each request is checked before anything else: only `POST` is accepted (405 otherwise), bodies are capped at 4 MiB (413), and a request whose `X-Signature-Ed25519` does not verify against the public key gets 401. Then:

- An interaction PING gets the PONG Discord expects.
- Any other interaction is answered with 202 and handed to the bot, which runs its command, route or modal handler as it would for the gateway; the handler answers through Discord's REST API, with `ctx.respond` and the rest. [Auto defer](responses.md#deferring) works the same.
- A webhook event reaches the bot's listeners and gets 204, as does the PING Discord sends to the Webhook Events URL.

`ASGIApp(bot, token=..., public_key=...)` takes the token, else `BOSTIE_TOKEN`, and the public key, else the one the application reports.

## With or without the gateway

Served alone, the app has no gateway connection: no gateway events, no cache updates, no voice. Commands, components, modals, REST and webhook events work. To have both, start the bot on the gateway as well, in the same event loop, and the app then uses the running bot; interactions then arrive over HTTP and the gateway still delivers events.

## Webhook events

| Event | Sent when |
|---|---|
| `ApplicationAuthorized` | a user added the app to a guild or to their account |
| `ApplicationDeauthorized` | a user removed the app, or its OAuth2 tokens ended |
| `EntitlementCreate`, `EntitlementUpdate`, `EntitlementDelete` | an entitlement changed |
| `QuestUserEnrollment` | a user was added to a Quest; Discord documents it but does not send it to apps yet |
| `LobbyMessageCreate`, `LobbyMessageUpdate`, `LobbyMessageDelete` | a message was sent, edited or deleted in a Social SDK lobby |
| `GameDirectMessageCreate`, `GameDirectMessageUpdate`, `GameDirectMessageDelete` | a direct message in a Social SDK session changed |

Listen to them with `@bot.listen` like gateway events. A webhook event type bostie does not model arrives as a `RawEvent`.

## In your own web server

The two pieces `ASGIApp` uses are public, for a web framework you already run:

```python
--8<-- "docs/snippets/http/own_server.py"
```

`verify_signature(public_key, signature, timestamp, body)` checks the `X-Signature-Ed25519` and `X-Signature-Timestamp` headers of a request against the app's hex public key; answer 401 when it returns `False`. `decode_webhook_event(body)` returns the typed event of a Webhook Events request, a `RawEvent` for a type bostie does not model, or `None` for the PING. For interactions in your own server, the [HTTP interactions example](../examples.md#http-interactions) shows the ASGI way, which is the one bostie supports.
