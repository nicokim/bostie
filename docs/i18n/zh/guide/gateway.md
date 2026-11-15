---
source: 3337dc9b9c1508c500eac3639bbfb9fc948f4a27
---

# Intent、在线状态与网关

网关是一条 WebSocket 连接，Discord 通过它发送事件，机器人则通过它发送在线状态和请求。bostie 的 Rust 核心负责保持连接：心跳、恢复、重连、压缩以及发送速率限制。本页介绍机器人可以向网关请求什么。

## Intent { #intents }

Discord 只会把事件发送给申请了相应 Intent 的机器人。`Bot(intents=...)` 接受一个 `Intents` 标志，省略时为 `Intents.DEFAULT`：除三个特权 Intent 之外的所有 Intent。

| 特权 Intent | 用于 |
|---|---|
| `GUILD_MEMBERS` | 成员加入、更新和移除事件，缓存全部成员，请求所有成员 |
| `GUILD_PRESENCES` | 在线状态更新以及缓存中的在线状态 |
| `MESSAGE_CONTENT` | 服务器中消息的文字内容、嵌入内容、附件和组件，提及机器人的消息除外；私信中获取这些内容不需要 Intent |

特权 Intent 还必须在 Developer Portal 的 **Bot** 页面上开启。否则 Discord 会以关闭码 4014 关闭连接，`start()` 会引发 `PrivilegedIntentsRequired`，其消息会告诉你在哪里开启。用 `|` 添加 Intent：`Intents.DEFAULT | Intents.MESSAGE_CONTENT`。当某个监听器需要机器人缺少的 Intent 时，bostie 会发出警告；当某个选项或请求需要时，则引发 `MissingIntent`。

语音需要 `GUILD_VOICE_STATES`，`DEFAULT` 中已包含它。

```python
--8<-- "docs/snippets/gateway/intent_presets.py"
```

除了单个 Intent，还有三个预设：`Intents.DEFAULT`（又名 `Intents.UNPRIVILEGED`）包含除特权 Intent 之外的所有 Intent，`Intents.PRIVILEGED` 包含三个特权 Intent，`Intents.ALL` 包含两者。`& ~` 可以从预设中去掉某些 Intent，就像上面那样。

## 在线状态 { #presence }

```python
--8<-- "docs/snippets/gateway/presence_and_requests.py"
```

`Bot(status=..., activities=[...], afk=False, idle_since=None)` 设置机器人连接时显示的内容。`await bot.update_presence(...)` 会在每个分片上修改它，之后连接的分片也会使用新值；省略的选项保持原值。

`status` 是一个 `Status`：`ONLINE`、`IDLE`、`DND` 或 `INVISIBLE`。`bostie.activity` 用于构建活动：

| 构建器 | 显示 |
|---|---|
| `activity.playing(name)` | 正在玩 *name* |
| `activity.listening(name)` | 正在听 *name* |
| `activity.watching(name)` | 正在看 *name* |
| `activity.competing(name)` | 正在参加 *name* |
| `activity.streaming(name, url)` | 正在直播 *name*，适用于 Discord 支持的直播服务 |
| `activity.custom(text)` | 内容为 *text* 的自定义状态 |

除 `custom` 外，每个构建器都接受 `state=`，即第二行文字，并返回一个 `activity.ActivityDict`。

## 请求 { #requests }

有些数据只能通过网关获取，作为对某个请求的应答：

- `await bot.request_members(guild_id, query="ab", limit=10)` 返回用户名或昵称以 `query` 开头的成员，最多 `limit` 个（0 到 100）。`user_ids=[...]` 则改为按 ID 请求最多 100 个成员。两者都不提供时，它会请求所有成员，这需要 `GUILD_MEMBERS`。`presences=True` 会附带这些成员的在线状态，需要 `GUILD_PRESENCES`。应答是一个 `RequestedMembers`，带有 `members`、`presences` 和 `not_found`（Discord 没有找到的 ID）。Discord 会对这个请求进行速率限制：被拒绝时会引发带有 `retry_after` 的 `MemberRequestRateLimited`。
- `await bot.request_soundboard_sounds(guild_id)` 返回该服务器的音效板音效。
- `await bot.request_channel_info(guild_id)` 返回该服务器各频道的语音状态和语音开始时间；`fields=["status"]` 可以请求更少的内容。

每个请求对应答的每一部分最多等待 30 秒（成员请求可用 `timeout=` 设置），超时后引发 `TimeoutError`。向正在开始新会话的分片发出的请求会引发 `StateError`；请在它就绪后重试。

## 原始网关命令 { #raw-gateway-commands }

`bot.gateway.send(op, d)` 可以发送任何网关命令。操作码 3（在线状态）会发送到每个分片；带有 `guild_id` 的载荷会发送到该服务器所在的分片，带有 `guild_ids` 的载荷会按各分片负责的 ID 分别发送，其他载荷则发送到分片 0。Discord 的应答以事件形式到达，而且只针对有监听的事件名，所以请把原始命令与一个 `listen_raw` 监听器或一个 `wait_for` 搭配使用。

`bot.gateway` 还提供：`shard_ids`，本进程运行的分片；`shard_for(guild_id)`，Discord 向其发送某个服务器事件的分片；`shard(shard_id)`，它有自己的 `send`、`reconnect` 和 `latency`；`reconnect()`，断开所有连接并在新连接上恢复会话；以及 `latency`，平均心跳往返时间。发往本进程未运行的分片的命令会引发 `ShardNotRunning`，排在过多命令之后的命令会引发 `GatewayQueueFull`。

## 背压 { #backpressure }

Rust 核心会以 Discord 发送的速度读取事件，并把它们交给 Python。当事件循环跟不上时，分片会暂停读取直到 Python 赶上来，同时记录一条警告，并发出带有等待字节数的 `ShardBackpressure`。处理函数或监听器中长时间的阻塞操作会导致这种情况；请用 `asyncio.to_thread` 把这类工作移到线程中。
