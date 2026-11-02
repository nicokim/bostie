# Running the bot

`bot.run()` owns the process: it starts an event loop, connects, and returns once the bot stops, closing cleanly on Ctrl+C and SIGTERM. When something else owns the event loop, such as a web server, start and close the bot yourself.

## start() and close()

`await bot.start()` connects and handles events until `await bot.close()` or a fatal error, which it raises. Run it as a task, wait until the bot is ready, and close it when your program ends:

```python
started = asyncio.create_task(bot.start())
try:
    await bot.wait_until_ready()
except bostie.StateError:
    await started
    raise
...
await bot.close()
await started
```

`wait_until_ready()` raises `StateError` when the bot stops before it is ready; awaiting the task then raises the reason, such as `InvalidToken`. `bot.rest` works from the `Starting` event on, before the gateway connects.

## In a web app

A web framework's lifespan hook is the place for it. With FastAPI:

```python
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

import bostie

bot = bostie.Bot()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    started = asyncio.create_task(bot.start())
    try:
        await bot.wait_until_ready()
    except bostie.StateError:
        await started
        raise
    yield
    await bot.close()
    await started


app = FastAPI(lifespan=lifespan)


@app.post("/channels/{channel_id}/announce")
async def announce(channel_id: int, text: str) -> dict[str, str]:
    message = await bot.rest.messages.create(channel_id, text)
    return {"id": str(message.id)}
```

[The aiohttp example](examples.md#a-web-app) does the same with aiohttp. Run one worker process per bot: two workers would run the bot twice.

## Setup and cleanup

`Starting` listeners run once per `start()`, before commands sync and the gateway connects, and `start()` waits for them: open database pools or HTTP clients there, or include routers that depend on them. `Stopped` listeners run at the end of `close()`. `Stopping` comes before it, while the gateway and REST still work, and only after `Started`.

An `ASGIApp` serving interactions over HTTP sends `Starting` and `Stopped` too when its lifespan starts and closes the bot, and runs the bot's tasks in between. With no gateway, no shard ever becomes ready: `Started` and `Stopping` never fire, and `wait_until_ready()` never returns.

## Several bots

Each `Bot` has its own connection to the Rust core, so one process can run several bots, each started and closed on its own.

## Event loops

`bot.run(loop_factory=uvloop.new_event_loop)` runs on another event loop. `bot.run()` configures logging at INFO unless the root logger is configured already; with `start()`, configure it yourself.
