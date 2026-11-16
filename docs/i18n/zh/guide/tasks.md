---
source: b58df394d71cd32e4c45cccfdd0da40ddcb957c3
description: "用 @bot.every 每隔几秒或在每天的固定时间运行后台任务，并在机器人运行时启动、停止、重启或重新安排它们。"
---

# 后台任务

![Bostie 在闹钟响个不停时呼呼大睡](../assets/mascot/bostie-sleeping.webp){ align=right width="180" loading=lazy }

`@bot.every` 会在机器人就绪期间，按计划运行一个不带参数的异步函数。

```python
--8<-- "docs/snippets/tasks/scheduled.py"
```

`every(seconds=300)` 会在机器人就绪后立即运行该函数，之后每 300 秒运行一次。计划不会漂移：每次运行都从第一次运行起算的下一个 `seconds` 整数倍时刻开始，耗时超过 `seconds` 的运行会跳过它所覆盖的那些时刻。

`every(time=...)` 会在每天的某个时刻运行它，或者在若干个时刻各运行一次。没有 `tzinfo` 的 `datetime.time` 按 UTC 计算。像上面那样使用固定偏移时，它会一直保持该偏移；使用 `zoneinfo.ZoneInfo` 时，它会跟随该时区的夏令时变化。`seconds` 和 `time` 只能二选一。

## 控制任务 { #controlling-a-task }

```python
--8<-- "docs/snippets/tasks/task_control.py"
```

`@bot.every` 返回一个 `Task`，await `check_feeds()` 仍然会运行一次该函数。它的方法可以在机器人运行期间修改计划：

| `Task` | 作用 |
|---|---|
| `stop()` | 取消任务，无论正在运行还是处于两次运行之间，并让它在 `start()` 之前不再随机器人启动；在机器人运行之前调用时，任务会等待 `start()` |
| `start()` | 在机器人就绪期间运行任务，若已就绪则从现在开始；已在运行的任务保持原样 |
| `restart()` | 取消任务并重新启动，因此间隔任务会立即运行 |
| `change_interval(seconds=...)` 或 `change_interval(time=...)` | 切换到新的计划；等待时间从修改时，或从正在进行的那次运行结束时重新计算 |
| `count` | 自任务上次启动以来已结束的运行次数 |
| `seconds`、`times` | 它所遵循的计划 |
| `@task.before`、`@task.after` | 注册 `async def hook()`，在任务每次启动和每次停止时 await，无论停止是由 `stop()`、`restart()`、路由器被移除还是 `close()` 引起的 |

钩子中的异常和运行中的异常一样，会作为 `TaskError` 发出。当 `before` 钩子抛出异常时，任务会保持停止状态，直到调用 `restart()`。

## 任务何时运行 { #when-tasks-run }

任务在机器人发出 `Started` 时启动，`close()` 会在关闭 HTTP 客户端之前取消它们。注册在一个稍后才被引入的路由器上的任务，会在路由器被引入后立即启动；所属路由器被移除的任务会被取消。只由 [`ASGIApp`](http-interactions.md) 提供服务的应用，会在应用启动时启动其任务。

一个函数只能注册为一个任务。要按两种计划运行同一段代码，请注册两个函数。

## 任务抛出异常时 { #when-a-task-raises }

运行中的异常会作为一个 `TaskError` 事件发出，带有 `task`（抛出异常的 `Task`）和 `error`，任务会在下一个时刻再次运行。如果没有 `TaskError` 监听器，bostie 会记录该异常及其回溯信息。
