# Running a bot

![Bostie celebrating with confetti](../assets/mascot/bostie-celebrating.webp){ align=right width="180" loading=lazy }

## `bot.run()`

`bot.run()` is the whole program for most bots. It configures logging at INFO on stderr, unless the root logger has handlers already, starts an event loop, and runs the bot until it stops. Ctrl+C and, on the main thread, SIGTERM close it cleanly.

The token comes from the `BOSTIE_TOKEN` environment variable, or from `bot.run(token)`. A leading `Bot ` is stripped, and a missing token raises `ValueError`. `run` also takes `loop_factory`, a function that creates the event loop, passed on to `asyncio.run`.

## The lifecycle

`bot.start()` does the work behind `run()`:

1. It sends the `Starting` event and awaits each of its listeners in turn. REST works here, the gateway does not; a listener that raises stops the start.
2. It [syncs the commands](registration.md), unless `auto_sync=False`.
3. It connects the gateway shards. When every shard is ready and its guilds have arrived, it sends `Started` once, starts the [background tasks](tasks.md) and lets `wait_until_ready()` return.
4. It handles events until `close()`, or until Discord closes the gateway for good, which raises, for example `InvalidToken` or `PrivilegedIntentsRequired`.

```python
--8<-- "docs/snippets/running/lifecycle_events.py"
```

`await bot.close()` shuts down in this order:

1. If `Started` was sent, it sends `Stopping` and gives its listeners up to 5 seconds while the gateway is still open.
2. It leaves every voice channel, then closes each shard, which ends its session with close code 1000, or saves it for a [resume](sharding.md#resuming-after-a-restart).
3. It cancels the background tasks. Handlers still running get `shutdown_grace` seconds, 0 by default, before they are cancelled too.
4. It closes the HTTP client and sends `Stopped`, whose listeners it awaits. `bot.http`, `bot.rest` and `bot.gateway` are gone.

`close()` can be called from anywhere, more than once, and while `start()` runs; the bot can be started again afterwards. `async with bot:` closes it when the block ends.

## Inside another program

When your program already runs an event loop, start the bot as a task:

```python
--8<-- "docs/snippets/running/inside_asyncio.py"
```

`await bot.wait_until_ready()` returns once `Started` was sent, at once if it was, and raises `StateError` if the bot stops first; awaiting the `start()` task then raises why. `bot.is_ready` says the same without waiting. The [web app example](../examples.md#web-app) runs a bot inside a web server's lifespan and posts messages from a web route.

A bot that only answers interactions over HTTP needs no gateway: see [HTTP interactions](http-interactions.md). `await bot.open()` starts it that way and returns: it opens the HTTP client, fetches the application and the bot's user, sends `Starting`, syncs the commands, starts the background tasks and returns the application. `ASGIApp` calls it, and `close()` ends it. A script that only calls the REST API uses a [`RESTClient`](rest.md#without-a-bot).

## While it runs

| Attribute | Value |
|---|---|
| `bot.user` | the bot's `CurrentUser`, once connected |
| `bot.application_id` | the app's id, once started |
| `bot.latency` | the mean heartbeat round trip of the shards in seconds, or `None` |
| `bot.is_running` | whether `start()` or `open()` runs the bot, until `close()` ends it |
| `bot.is_ready` | whether `Started` was sent since the last `start()` |
| `bot.rest`, `bot.http` | the [REST API](rest.md), once started |
| `bot.gateway` | the [gateway shards](gateway.md#raw-gateway-commands), once connected |
| `bot.cache` | the [cache](cache.md) |
| `bot.voice` | the [voice connections](../voice/index.md) |
| `bot.auto_defer` | the default [auto defer](responses.md#deferring) delay; can be changed at any time |

`bot.rest`, `bot.http` and `bot.gateway` raise `StateError` before `start()`.

## Logging

bostie logs to the standard `logging` module under these names:

| Logger | What it reports |
|---|---|
| `bostie.bot` | handlers that failed or did not answer, intents a listener needs, unhandled interactions |
| `bostie.gateway` | connections, disconnects, resumes, backpressure, invalid requests |
| `bostie.rest` | rate limits and retries, from the Rust HTTP client |
| `bostie.sync` | what each command sync changed |
| `bostie.context` | answers that could not apply, such as an ephemeral flag on an edit |
| `bostie.events` | payloads that did not fit their event, once per event name |
| `bostie.voice` | voice connections, DAVE, sinks, receivers and players |
| `bostie.bridge` | the link between Python and the Rust core |

The Rust core logs through the same loggers, and reads their levels when the bot starts: set `logging.getLogger("bostie.gateway").setLevel(logging.DEBUG)` before `run()` to see the gateway in detail.

## Pointing bostie elsewhere

`Bot(api_url=...)` sends REST requests to another base URL, such as a rate-limit proxy shared by several processes or a [fake Discord](testing.md) in tests. Left out, bostie reads `BOSTIE_API_URL` when the bot starts, and uses `https://discord.com/api/v10` when that is unset too. Behind a rate-limit proxy, pass `http_settings=HTTPSettings(rate_limiter=False)`, so bostie leaves the waiting to the proxy. The [REST guide](rest.md#rate-limits-retries-and-timeouts) covers the other HTTP settings.

## Several bots in one process

Each `Bot` has its own connection to the Rust core, its own cache and its own HTTP client, so one process can run several bots, each started and closed on its own, in one event loop.

## Keeping state

A bot is an ordinary object: subclass `Bot` to give it attributes, as the [lifecycle example](../examples.md#lifecycle) does with a database connection opened in a `Starting` listener and closed in a `Stopped` one. Every `Bot` option is listed in the [Bot options reference](../reference/bot-options.md).
