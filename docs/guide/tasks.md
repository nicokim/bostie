# Background tasks

`@bot.every` runs an async function without arguments on a schedule, while the bot is ready.

```python
--8<-- "docs/snippets/tasks/scheduled.py"
```

`every(seconds=300)` runs the function as soon as the bot is ready, then every 300 seconds. The schedule does not drift: each run starts on the next multiple of `seconds` from the first, and a run that takes longer than `seconds` skips the ticks it overlapped.

`every(time=...)` runs it every day at one time of day, or at each of several. A `datetime.time` without `tzinfo` is in UTC. With a fixed offset, as above, it stays at that offset; with a `zoneinfo.ZoneInfo`, it follows the zone's daylight saving changes. Pass `seconds` or `time`, not both.

## When tasks run

Tasks start when the bot sends `Started`, and `close()` cancels them before it closes the HTTP client. A task registered on a router that is included later starts as soon as it is included, and one whose router is excluded is cancelled. An app served only by [`ASGIApp`](http-interactions.md) starts its tasks when the app starts.

A function can be registered as a task once. To run the same code on two schedules, register two functions.

## When a task raises

An exception in a run is sent as a `TaskError` event with the `task` and the `error`, and the task runs again at its next time. Without a `TaskError` listener, bostie logs the exception with its traceback.
