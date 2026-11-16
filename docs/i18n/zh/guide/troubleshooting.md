---
source: e7704fd7192c52cc55342cad633590bd6f185904
description: "根据日志行解决 bostie 的常见问题：“The application did not respond”、收不到事件、网关和 REST 警告、语音问题和报告 bug。"
---

# 故障排查

![Bostie 苦思冥想，直到问号变成一个灯泡](../assets/mascot/bostie-thinking.webp){ align=right width="180" loading=lazy }

bostie 能发现的每个错误都会记录一条警告。本页按你观察到的现象列出这些问题，附上要查找的日志行和解决办法。

## 命令显示“The application did not respond” { #a-command-says-the-application-did-not-respond }

| 日志行 | 原因 | 解决办法 |
|---|---|---|
| `nothing handles interaction ... (command 'name')` | Discord 上仍有一条正在运行的机器人没有定义的命令，通常是在 `auto_sync=False` 下改名之后，或者该命令属于另一个进程 | 同步命令（[注册命令](registration.md)） |
| `nothing handles interaction ... (custom_id '...')` | 没有任何 `on_component` 或 `on_modal` 模板能匹配该自定义 ID | 对照你发送的 ID 检查模板；用 `route.id(...)` 来构建 ID |
| `... returned without answering` | 处理函数结束时没有调用 `respond`、`defer` 或 `modal` | 在每条代码路径上都作出回应，包括提前返回的路径 |
| `... returned after deferring without answering` | 它延迟了回应却始终没有回应，于是“thinking…”会一直显示到令牌过期 | 在耗时的工作完成后调用 `respond` |
| `interaction ... reached its handler ... s after it was received; something is blocking the event loop` | 处理函数或监听器中的阻塞代码占住了事件循环，超过了 Discord 的 3 秒 | 把这项工作移到 `asyncio.to_thread`，或调低 `auto_defer` |
| `interaction ... was replayed ... s after it was created; its token is dead` | 该交互是在恢复会话的重放中到达的，已经来不及回应 | 无需处理；用户可以再执行一次 |

处理函数中未处理的异常会连同回溯信息一起记录，用户会看到“Something went wrong.”：添加一个[错误钩子](checks.md#error-hooks)来回应更合适的内容。

## 收不到事件 { #events-do-not-arrive }

- `... listens to EVENT, which needs the INTENT intent; add it to Bot(intents=...)`：机器人没有申请该 Intent。添加它；如果是特权 Intent，还要在 Developer Portal 中开启。
- `start()` 引发 `PrivilegedIntentsRequired`：机器人申请了 Developer Portal 中未允许的特权 Intent。在 **Bot** → **Privileged Gateway Intents** 下开启它，或者从 `intents` 中移除它。
- 在服务器中 `message.content` 为空：缺少 `MESSAGE_CONTENT` Intent。
- `EVENT has an unexpected shape (...); typed listeners skip it`：Discord 发送的载荷与事件类不匹配，每个事件名只记录一次。`RawEvent` 监听器仍然会收到它；请报告这一行日志。
- 更新事件上的 `old` 为 `None`：缓存中没有该对象；缓存哪些内容请参见[缓存](cache.md#what-it-keeps)。

## 网关 { #the-gateway }

- `shard N paused reading: ... bytes of events are waiting because the event loop is not keeping up`：有东西阻塞了事件循环；参见[背压](gateway.md#backpressure)。
- `close code N (...) means Discord refused what bostie sent; this is likely a bostie bug, please report it`：关闭码 4001、4002、4005 和 4008 表示 bostie 发送了 Discord 拒绝的内容。
- `the members of guild N stopped arriving; the cache lacks some`：用于填充缓存的成员请求超时了，每个分块的超时时间为 30 秒。
- `start()` 引发 `InvalidToken`：令牌错误或已被重置；请从 **Bot** 页面重新复制。

## REST { #rest }

- `N invalid requests (401, 403, 429) in the last 10 minutes; Discord bans the IP at 10 000`：机器人不断发出被 Discord 拒绝的请求，通常是缺少权限或 Webhook 已失效。从这些调用抛出的异常中找出失败的调用。
- 请求还没到达 Discord 就引发了 `RateLimited`：该路由被限制的时间超过了 `max_rate_limit_wait`；在 [`HTTPSettings`](rest.md#rate-limits-retries-and-timeouts) 中调高它，或稍后重试。

## 回应 { #answers }

- `ephemeral=... is ignored because the message being edited is ...`：消息发送之后就不能在仅自己可见和公开之间切换。
- `Discord allows 5 follow-ups to an interaction of an app installed only to the user`：用户安装的应用的第 6 条后续消息会失败。
- `the error hook ... left interaction ... unanswered; bostie answered 'Something went wrong.'`：让该钩子作出回应。

## 语音 { #voice }

- `nobody hears the bot in guild N: it is server muted`，或 `it is suppressed on this stage; use vc.stage('request')`：版主将机器人服务器静音了，或者它在舞台上是听众。
- `a moderator deafened the bot in guild N, so Discord sends it no audio`：在取消拒听之前，接收器收不到任何内容。
- 带有原因 `TIMEOUT` 的 `VoiceConnectError`：Discord 没有在 `timeout` 秒内回应加入请求；频道可能已满，或者机器人缺少“连接”权限。
- `MissingIntent: voice needs Intents.GUILD_VOICE_STATES`：语音需要该 Intent，`Intents.DEFAULT` 中已包含它。

## 报告 bug { #reporting-a-bug }

`python -m bostie` 会打印需要附上的版本信息，对于语音问题，`bostie.voice.versions()` 还会补充 libopus 和 DAVE 的版本。在启动机器人之前，把相关日志记录器设为 `DEBUG`，参见[日志](running.md#logging)，然后附上问题发生前后的日志行，并删除其中的令牌。
