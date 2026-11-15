---
source: c45c92d8551bab86affc3711ec78970f48e886d8
---

# 分片与恢复

![三只 Bostie 排成一行奔跑](../assets/mascot/bostie-running.webp){ align=right width="220" loading=lazy }

Discord 会把机器人所在的服务器分配到多条网关连接上，这些连接称为分片：服务器 `g` 属于分片 `(g >> 22) % shard_count`。加入超过 2500 个服务器的机器人必须分片；Discord 会以关闭码 4011 关闭未分片的连接，`start()` 会引发 `ShardingRequired`。

## 单进程中的分片 { #shards-in-one-process }

默认的 `Bot(shard_count="auto")` 会向 Discord 询问应使用多少个分片，并全部运行。无需任何其他操作：bostie 会在 Discord 的 identify 限制内启动各分片，每个分片都把事件发送给同一组监听器、缓存和处理函数。`event.shard_id` 表示事件来自哪个分片。

## 跨进程的分片 { #shards-across-processes }

```python
--8<-- "docs/snippets/sharding/cluster.py"
```

`shard_count=16` 固定分片总数，`shard_ids` 选择本进程运行哪些分片；`shard_ids` 需要显式指定 `shard_count`，其中的 ID 必须互不相同，且在 `range(shard_count)` 之内。

Discord 限制每个机器人同时进行的 identify 次数。bostie 在单个进程内会遵守这一限制；跨进程时，则由 `before_identify` 进行协调。bostie 在每次 identify 之前会 await `before_identify(shard_id)`，因此它可以在你的各进程共享的锁上等待一个空位。在这个例子中，`identify_lock` 代表这样一个共享锁，并按 Discord 的要求，在同一个桶的两次 identify 之间保持 5 秒。如果该钩子抛出异常，bostie 会记录错误并照常进行 identify。

## 分片事件 { #shard-events }

| 事件 | 字段 | 何时发出 |
|---|---|---|
| `ShardReady` | `shard_id` | 分片的新会话已就绪，且其服务器都已到达 |
| `ShardDisconnect` | `shard_id`、`code`、`reason`、`resuming` | 连接断开；`resuming` 为真时，分片会恢复会话并重放错过的事件，否则会开始新会话 |
| `ShardStop` | `shard_id`、`code`、`reason` | 分片永久停止，原因是调用了 `close()` 或 Discord 终止了它 |
| `ShardBackpressure` | `shard_id`、`queued_bytes` | 由于事件循环跟不上，分片暂停了读取 |

重连和恢复都不需要你做任何事。`bot.latency` 是各分片心跳往返时间的平均值，`bot.gateway.shard(shard_id).latency` 是单个分片的值。

## 重启后恢复 { #resuming-after-a-restart }

重启通常会在每个分片上开始新会话，期间几分钟内的事件就丢失了。设置 `Bot(session_file="sessions.json")` 后，`close()` 会保持会话打开，并把它们写入该文件。下一次 `start()` 会读取并删除该文件，然后恢复每个与当前 `shard_count` 和分片 ID 相匹配的已保存会话，于是 Discord 会重放机器人停机期间发生的事件。无法读取的文件会被记录并忽略，没有已保存会话的分片会进行 identify。

`bot.run()` 会在收到 Ctrl+C 和 SIGTERM 时关闭，因此由进程管理器或部署触发的重启会保存会话。崩溃则不会，下一次启动会进行 identify。在创建超过 5 秒后才被重放的交互会被跳过并发出警告，因为它的令牌已无法及时回应。
