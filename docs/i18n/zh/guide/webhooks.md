---
source: efdcb485cd6bec9ca515ad0f0311945137caa315
description: "无需机器人，用异步或普通 Python 代码通过 Discord Webhook URL 发送、编辑和删除消息，也可以用机器人创建和管理 Webhook。"
---

# Webhook

Webhook 以自己的名称和头像向某一个频道发布消息。它的 URL 中包含一个 ID 和一个令牌，只要有令牌就能发布消息，因此部署脚本或监控任务无需机器人也能发送消息。

```python
--8<-- "docs/snippets/webhooks/deploy_notice.py"
```

`WebhookClient(url)` 从 Webhook URL 中解析出 ID 和令牌，对于其他任何内容则引发 `ValueError`。请在 `async with` 中使用它，它会打开和关闭自己的 HTTP 客户端。`SyncWebhookClient(url)` 为没有事件循环的代码提供同样的方法；每次调用都运行自己的事件循环。

| 方法 | 作用 |
|---|---|
| `execute(content, **parts)` | 发布一条消息，并在 Discord 保存后返回它 |
| `edit_message(message_id, **parts)` | 编辑该 Webhook 发送的消息；省略的部分保持不变，`None` 会清除它 |
| `fetch_message(message_id)`、`delete_message(message_id)` | 获取或删除一条消息 |
| `fetch()`、`edit(name=..., avatar=...)`、`delete()` | 操作 Webhook 本身 |
| `execute_slack(payload)`、`execute_github(payload)` | 以 Discord 的 Slack 兼容端点和 GitHub 兼容端点所接受的格式发布载荷 |

`execute` 接受各[消息部分](messages.md)，此外还接受：

- `username` 和 `avatar_url`，在这条消息中代替 Webhook 自己的名称和头像显示；
- `thread_id`，用于在频道的某个子区中发布；消息相关的方法也接受它；
- `thread_name`，用于在论坛频道或媒体频道中创建子区，可配合 `applied_tags` 使用；
- `with_components=True`，在不属于任何应用的 Webhook 上使用组件时需要设置。

这些客户端和机器人一样接受 `api_url`、`http_settings` 和 `allowed_mentions`；默认情况下，Webhook 可以通知用户和身份组，但不能通知 `@everyone`。

当 Discord 回应 404 Unknown Webhook 时，说明该 Webhook 已被删除。之后 bostie 会对该 Webhook 的每个请求直接给出同样的 404，而不发送它，因此一个失效的 Webhook 不会计入[无效请求上限](rest.md#rate-limits-retries-and-timeouts)。

## 用机器人管理 Webhook { #managing-webhooks-with-a-bot }

`bot.rest.webhooks` 用于创建和列出某个频道或服务器的 Webhook，这需要“管理 Webhook”权限；它也提供基于令牌的方法：

- `create(channel_id, name=..., avatar=File(...))` 返回一个 `Webhook`，其 `url` 可以交给 `WebhookClient` 使用。
- `list(channel_id)`、`list_guild(guild_id)`、`fetch(webhook_id)`、`edit(webhook_id, ...)` 和 `delete(webhook_id)` 使用机器人的令牌。
- `execute(webhook_id, token, ...)` 以及其他 `_with_token` 方法和消息相关方法，就是 `WebhookClient` 所调用的那些方法。

`WebhooksUpdate` 事件表示某个频道的 Webhook 发生了变化。Webhook 发送的消息以 `WebhookMessage` 的形式到达，其 `author` 是 `PartialUser`，并且设置了 `webhook_id`。
