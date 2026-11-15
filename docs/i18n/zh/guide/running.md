---
source: fecb554b875bf1b19567e85926b3784beca4dba5
---

# 运行机器人

![Bostie 在彩纸中欢庆](../assets/mascot/bostie-celebrating.webp){ align=right width="180" loading=lazy }

## `bot.run()` { #botrun }

对大多数机器人来说，`bot.run()` 就是整个程序。它会在 stderr 上配置 INFO 级别的日志（除非根日志记录器已有处理器），启动一个事件循环，并运行机器人直到它停止。Ctrl+C，以及在主线程上收到的 SIGTERM，都会干净地关闭它。

令牌来自 `BOSTIE_TOKEN` 环境变量，或者来自 `bot.run(token)`。开头的 `Bot ` 会被去掉，缺少令牌则引发 `ValueError`。`run` 还接受 `loop_factory`，一个创建事件循环的函数，会被传给 `asyncio.run`。

## 生命周期 { #the-lifecycle }

`bot.start()` 完成 `run()` 背后的工作：

1. 它发出 `Starting` 事件，并依次 await 每个监听器。此时 REST 可用，网关不可用；抛出异常的监听器会中止启动。
2. 它会[同步命令](registration.md)，除非设置了 `auto_sync=False`。
3. 它连接各个网关分片。当每个分片都已就绪、并且分片上的服务器都已到达时，它会发出一次 `Started`，启动[后台任务](tasks.md)，并让 `wait_until_ready()` 返回。
4. 它持续处理事件，直到调用 `close()`，或者直到 Discord 永久关闭网关，此时会抛出异常，例如 `InvalidToken` 或 `PrivilegedIntentsRequired`。

```python
--8<-- "docs/snippets/running/lifecycle_events.py"
```

`await bot.close()` 按以下顺序关闭：

1. 如果已经发出过 `Started`，它会发出 `Stopping`，并在网关仍然打开时给其监听器最多 5 秒时间。
2. 它离开每个语音频道，然后关闭每个分片，以关闭码 1000 结束其会话，或者保存会话以便之后[恢复](sharding.md#resuming-after-a-restart)。
3. 它取消后台任务。仍在运行的处理函数会有 `shutdown_grace` 秒（默认为 0）的时间，之后同样会被取消。
4. 它关闭 HTTP 客户端，并发出 `Stopped`，await 其监听器。此后 `bot.http`、`bot.rest` 和 `bot.gateway` 都不再可用。

`close()` 可以从任何地方调用，可以调用多次，也可以在 `start()` 运行时调用；之后机器人还可以再次启动。`async with bot:` 会在代码块结束时关闭它。

## 在其他程序中运行 { #inside-another-program }

如果你的程序已经运行着一个事件循环，就把机器人作为一个任务启动：

```python
--8<-- "docs/snippets/running/inside_asyncio.py"
```

`await bot.wait_until_ready()` 会在发出 `Started` 后返回（若已发出则立即返回），如果机器人先停止了，则引发 `StateError`；此时 await `start()` 任务会抛出具体原因。`bot.is_ready` 无需等待即可得知同样的信息。[Web 应用示例](../examples.md#web-app)在 Web 服务器的 lifespan 中运行机器人，并从 Web 路由发送消息。

只通过 HTTP 回应交互的机器人不需要网关：参见 [HTTP 交互](http-interactions.md)。`await bot.open()` 以这种方式启动机器人并返回：它打开 HTTP 客户端，获取应用信息和机器人的用户，发出 `Starting`，同步命令，启动后台任务，并返回应用。`ASGIApp` 会调用它，`close()` 则结束它。只调用 REST API 的脚本使用 [`RESTClient`](rest.md#without-a-bot)。

## 运行期间 { #while-it-runs }

| 属性 | 值 |
|---|---|
| `bot.user` | 机器人的 `CurrentUser`，连接后可用 |
| `bot.application_id` | 应用的 ID，启动后可用 |
| `bot.latency` | 各分片心跳往返时间的平均值（秒），或 `None` |
| `bot.is_running` | `start()` 或 `open()` 是否正在运行机器人，直到 `close()` 结束它 |
| `bot.is_ready` | 自上次 `start()` 以来是否已发出 `Started` |
| `bot.rest`、`bot.http` | [REST API](rest.md)，启动后可用 |
| `bot.gateway` | [网关分片](gateway.md#raw-gateway-commands)，连接后可用 |
| `bot.cache` | [缓存](cache.md) |
| `bot.voice` | [语音连接](../voice/index.md) |
| `bot.auto_defer` | 默认的[自动延迟回应](responses.md#deferring)时间；随时可以修改 |

在 `start()` 之前访问 `bot.rest`、`bot.http` 和 `bot.gateway` 会引发 `StateError`。

## 日志 { #logging }

bostie 使用标准的 `logging` 模块，以下列名称记录日志：

| 日志记录器 | 报告内容 |
|---|---|
| `bostie.bot` | 失败或未回应的处理函数、监听器需要的 Intent、未处理的交互 |
| `bostie.gateway` | 连接、断开、恢复、背压、无效请求 |
| `bostie.rest` | 来自 Rust HTTP 客户端的速率限制和重试 |
| `bostie.sync` | 每次命令同步改变了什么 |
| `bostie.context` | 无法生效的回应，例如编辑时设置的仅自己可见标志 |
| `bostie.events` | 与其事件不匹配的载荷，每个事件名记录一次 |
| `bostie.voice` | 语音连接、DAVE、sink、接收器和播放器 |
| `bostie.bridge` | Python 与 Rust 核心之间的连接 |

Rust 核心也通过这些日志记录器输出日志，并在机器人启动时读取它们的级别：在 `run()` 之前设置 `logging.getLogger("bostie.gateway").setLevel(logging.DEBUG)`，就能看到网关的详细信息。

## 让 bostie 指向别处 { #pointing-bostie-elsewhere }

`Bot(api_url=...)` 会把 REST 请求发送到另一个基础 URL，例如多个进程共享的速率限制代理，或测试中的[模拟 Discord](testing.md)。如果省略，bostie 会在机器人启动时读取 `BOSTIE_API_URL`，若它也未设置，则使用 `https://discord.com/api/v10`。在速率限制代理后面时，请传入 `http_settings=HTTPSettings(rate_limiter=False)`，让 bostie 把等待交给代理处理。其他 HTTP 设置请参见 [REST 指南](rest.md#rate-limits-retries-and-timeouts)。

## 在一个进程中运行多个机器人 { #several-bots-in-one-process }

每个 `Bot` 都有自己与 Rust 核心的连接、自己的缓存和自己的 HTTP 客户端，因此一个进程可以在同一个事件循环中运行多个机器人，每个都可以单独启动和关闭。

## 保存状态 { #keeping-state }

机器人就是一个普通对象：继承 `Bot` 来给它添加属性，就像[生命周期示例](../examples.md#lifecycle)那样，在 `Starting` 监听器中打开数据库连接，在 `Stopped` 监听器中关闭它。所有 `Bot` 选项都列在 [Bot 选项参考](../reference/bot-options.md)中。
