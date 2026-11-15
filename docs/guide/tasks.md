# Background tasks

`@bot.every` runs an async function without arguments on a schedule, while the bot is ready.

```python
--8<-- "docs/snippets/tasks/scheduled.py"
```

`every(seconds=300)` runs the function as soon as the bot is ready, then every 300 seconds. The schedule does not drift: each run starts on the next multiple of `seconds` from the first, and a run that takes longer than `seconds` skips the ticks it overlapped.

`every(time=...)` runs it every day at one time of day, or at each of several. A `datetime.time` without `tzinfo` is in UTC. With a fixed offset, as above, it stays at that offset; with a `zoneinfo.ZoneInfo`, it follows the zone's daylight saving changes. Pass `seconds` or `time`, not both.

## Controlling a task

```python
--8<-- "docs/snippets/tasks/task_control.py"
```

`@bot.every` returns a `Task`, and awaiting `check_feeds()` still runs the function once. Its methods change the schedule while the bot runs:

| `Task` | Does |
|---|---|
| `stop()` | cancels the task, in a run or between runs, and keeps it from starting with the bot until `start()`; called before the bot runs, the task waits for `start()` |
| `start()` | runs the task while the bot is ready, from now if it is; a running task goes on as it was |
| `restart()` | cancels the task and starts it anew, so an interval task runs at once |
| `change_interval(seconds=...)` or `change_interval(time=...)` | switches to the new schedule; the wait starts afresh from the change, or from the end of a run going on |
| `count` | how many runs ended since the task last started |
| `seconds`, `times` | the schedule it follows |
| `@task.before`, `@task.after` | register `async def hook()`, awaited each time the task starts and each time it stops, whether by `stop()`, `restart()`, an excluded router or `close()` |

An exception in a hook is sent as a `TaskError`, like one in a run. When the `before` hook raises, the task stays stopped until `restart()`.

## When tasks run

Tasks start when the bot sends `Started`, and `close()` cancels them before it closes the HTTP client. A task registered on a router that is included later starts as soon as it is included, and one whose router is excluded is cancelled. An app served only by [`ASGIApp`](http-interactions.md) starts its tasks when the app starts.

A function can be registered as a task once. To run the same code on two schedules, register two functions.

## When a task raises

An exception in a run is sent as a `TaskError` event with the `task`, the `Task` that raised, and the `error`, and the task runs again at its next time. Without a `TaskError` listener, bostie logs the exception with its traceback.
