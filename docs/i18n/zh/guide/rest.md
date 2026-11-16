---
source: 18ff3457c37f07dad7f0bcfc3aee4b5f5383fac0
description: "调用 Discord REST API 中所有面向机器人的端点：分页、批量清除消息、审核日志、错误、速率限制、重试、文件下载和原始请求。"
---

# REST API

![Bostie 跃起去接一个球](../assets/mascot/bostie-jumping.webp){ align=right width="180" loading=lazy }

对于 Discord REST API 中机器人可以调用的每个操作，`bot.rest` 都提供了一个方法，并按其操作的资源分组：`bot.rest.messages.create`、`bot.rest.members.add_role`、`bot.rest.guilds.fetch`。请求经由 bostie 的 Rust HTTP 客户端发出，它会遵守 Discord 的速率限制。

```python
--8<-- "docs/snippets/rest/rest_calls.py"
```

## 约定 { #conventions }

- URL 中的 ID 排在最前面，仅限位置传参，顺序与 URL 中一致：`bot.rest.members.add_role(guild_id, user_id, role_id)`。
- 其余参数都是关键字参数，命名与 Discord 文档一致，并按每个操作对应的 `TypedDict` 检查，因此拼写错误既是类型错误，也会在运行时引发 `TypeError`。
- Discord 会记入审核日志的操作接受 `reason=`，长度为 1 到 512 个字符，会显示给服务器的版主。
- 图片或声音字段，例如表情符号的 `image` 或服务器的 `icon`，接受一个 `File`；bostie 会把它作为 Discord 期望的 data URI 发送。权限字段接受 `Permissions`。
- 表情符号，例如 `reactions.create` 添加的那个，接受一个 Unicode 表情符号、一个写成 `<:name:id>`、`<a:name:id>` 或 `name:id` 的自定义表情符号，或一个 `Emoji` 或 `PartialEmoji` 模型。
- 名为 `fetch` 的方法获取单个对象，`list` 遍历多个对象，`edit` 修改，`create` 创建，`delete` 删除。其他动词沿用文档中的用词，例如 `members.add_role` 或 `guilds.begin_prune`。
- 方法返回模型，当 Discord 回应无内容时返回 `None`。它们不会修改 `bot.cache`，唯一的例外是 `channels.create_dm` 会在 `max_dm_channels` 允许时保存私信频道；变更之后的网关事件会更新缓存。

## 分页 { #paging }

对于 Discord 分页的端点，其 `list` 方法返回一个异步迭代器，会随着遍历自动获取下一页：`async for message in bot.rest.messages.list(channel_id, limit=500):`，就像上面的 `/today` 那样。不设置 `limit` 时，它会遍历所有项目。`before`、`after` 和 `around` 可以从其他位置开始，三者最多用一个；它们接受一个 ID 或一个 `datetime`，bostie 会把后者转换为该时刻的 ID。消息按从新到旧排列，使用 `after` 时则按从旧到新。对于 Discord 不分页的端点，其 `list` 方法是一个返回元组的协程。`audit_log.list` 产出完整的 `AuditLogPage` 对象，其中包含条目所引用的用户、Webhook 和其他对象。

## 批量清除消息 { #purging-messages }

`messages.purge(channel_id, limit=100, check=None)` 从新到旧遍历最近的 `limit` 条消息，删除使 `check` 返回真值的消息，若没有 `check` 则全部删除；`limit=None` 会遍历整个频道，`before` 和 `after` 可以从其他位置开始，与 `list` 相同。不到 14 天的消息以每批最多 100 条的方式批量删除，更早的消息则每条一次请求，因为 Discord 只允许批量删除较新的消息；Discord 不允许删除的类型，例如子区的起始消息，会被跳过。`bulk=False` 会逐条删除，就像在私信中那样，因为私信不支持批量删除。它返回已删除的消息，`reason` 会写入审核日志。批量删除和删除他人的消息需要“管理消息”权限。

```python
--8<-- "docs/snippets/rest/purge.py"
```

## 审核日志 { #the-audit-log }

`audit_log.list(guild_id)` 产出 `AuditLogPage` 对象，网关会把每个新条目作为 `AuditLogEntryCreate` 事件发送。条目的 `changes` 中，每个被修改的字段对应一个 `AuditLogChange`，其 `new_value` 和 `old_value` 的类型与被修改对象中该字段的类型一致：身份组的 `permissions` 为 `Permissions`，成员的 `communication_disabled_until` 为 `datetime`，频道的 `permission_overwrites` 为 `Overwrite` 元组。Discord 的特殊情况遵循其文档：成员身份组上的 `$add` 和 `$remove` 保存 `AuditLogRole` 对象，Webhook 的 `avatar_hash` 和邀请的 `channel_id` 分别采用 `avatar` 和 ID 的类型，命令权限变更则以身份组、用户或频道的 ID 为键，保存一个 `CommandPermission`。bostie 不认识的键，或形态与字段不符的值，会保留其 JSON 值，Discord 省略的值为 `None`。

```python
--8<-- "docs/snippets/rest/audit_log.py"
```

## 错误 { #errors }

Discord 以错误状态码回应的请求会引发 `HTTPException`：400 为 `BadRequest`，403 为 `Forbidden`，404 为 `NotFound`，5xx 为 `ServerError`。该异常带有 `status`、以 `JSONErrorCode` 表示的 Discord `code`、它的 `message`、按字段划分的 `errors`，以及原始的 `body`；它的文本会把每个字段错误单独列为一行。`UnexpectedResponse` 表示 Discord 接受了请求，但其回应无法解码；它的消息会说明变更是否已生效，`error.raw` 保存该回应。

## 速率限制、重试与超时 { #rate-limits-retries-and-timeouts }

Rust 客户端会跟踪每个路由的速率限制桶以及 Discord 的全局限制，并在桶有余量之前暂缓请求。`Bot(http_settings=HTTPSettings(...))` 可以改变其行为：

| `HTTPSettings` 字段 | 默认值 | 含义 |
|---|---|---|
| `global_rate_limit` | 50 | 所有路由合计的每秒请求数；如果 Discord 给了机器人更高的限额，可以调高 |
| `max_rate_limit_wait` | 30.0 | 因速率限制而等待的最长时间；需要等待更久的请求会直接引发 `RateLimited`，不会被发送 |
| `retries` | 2 | 失败请求的重试次数，采用指数退避，退避等待最多 `max_rate_limit_wait` |
| `connect_timeout` | 10.0 | 建立连接的秒数 |
| `read_timeout` | 60.0 | 等待回应的秒数 |
| `rate_limiter` | `True` | 设为 `False` 时立即发送，只等待返回的 429，适用于在速率限制代理之后使用 |
| `proxy` | `None` | 所有请求使用的 HTTP 或 HTTPS 代理 URL，包括 `voice.Url` 音频和 CDN 下载，凭据写在 URL 中，如 `http://user:password@host:3128` |

429 会在 Discord 给出的延迟之后重试，前提是该延迟在 `max_rate_limit_wait` 之内。500、502、503、504 或 524，或者连接中断，会对除 `POST` 外的所有方法重试 `retries` 次，因为 `POST` 可能已经生效；从未建立的连接则对所有方法都会重试。

Discord 会封禁在 10 分钟内发出 10 000 个无效请求（401、403、429）的 IP。bostie 会统计这些请求，并在达到 1000、5000 和 9000 时记录警告。达到 9500 时，它会拒绝每个请求并引发 `RequestBudgetExhausted`，而不是发送它，直到最近 11 分钟内的计数降到 9000 以下。

## 任意端点 { #any-endpoint }

`bot.http.request(method, route, **params)` 通过同一个客户端发送任意请求，并返回解码后的 JSON，回应为空时返回 `None`。路由保留其占位符，由关键字参数填充：`await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=123)`。它接受 `json=`、用于 multipart 上传的 `files=`、用于具名表单字段的 `form=`、用于 form-urlencoded 请求体的 `urlencoded=`、`query=` 和 `reason=`，并引发相同的异常。`bot.http.send(...)` 接受同样的参数，返回回应的原始字节，适用于不以 JSON 回应的端点。

## 下载文件 { #downloading-files }

附件和图片位于 Discord 的 CDN 上，而不在 API 之后，因此下载既不发送令牌，也不消耗速率限制额度。`bot.http` 通过同一个 Rust 客户端获取它们，同样使用其超时、重试和 `proxy` 设置：

| 方法 | 作用 |
|---|---|
| `read(source)` | 返回字节 |
| `save(source, path)` | 把它们写入 `path` 处的文件 |
| `to_file(source)` | 返回一个可以再次上传的 `File`；来自 `Attachment` 时保留文件名、描述、标题、剧透标记和语音消息的信息，来自 URL 时以其路径的最后一段命名 |

`source` 是一个 `Attachment`，或者一个 URL，例如 [`bostie.cdn`](formatting.md) 辅助函数构建的 URL。文件不存在时引发 `NotFound`。

```python
--8<-- "docs/snippets/rest/downloads.py"
```

## 不使用机器人 { #without-a-bot }

只需要 REST 的脚本或 Web 后端使用 `RESTClient`：

```python
--8<-- "examples/rest_script.py"
```

`async with RESTClient(token) as client:` 打开 HTTP 客户端；`client.rest` 拥有 `bot.rest` 的所有分组，`client.http` 是原始客户端。令牌默认取自 `BOSTIE_TOKEN`，客户端和机器人一样接受 `api_url`、`http_settings` 和 `allowed_mentions`。[OAuth2 指南](oauth2.md)介绍了用用户令牌调用 API 的 `BearerClient`，[Webhook 指南](webhooks.md)介绍了完全不需要令牌的客户端。

## 分组 { #the-groups }

| `bot.rest.` | 方法 |
|---|---|
| `application` | `edit`、`fetch`、`fetch_activity_instance`、`fetch_bot_application`、`list_role_connection_metadata`、`update_role_connection_metadata` |
| `audit_log` | `list` |
| `auto_moderation` | `create`、`delete`、`edit`、`fetch`、`list` |
| `bans` | `bulk_create`、`create`、`fetch`、`list`、`remove` |
| `channels` | `add_recipient`、`create`、`create_dm`、`create_group_dm`、`delete`、`delete_permission`、`edit`、`edit_permissions`、`edit_positions`、`fetch`、`fetch_member_permissions`、`fetch_role_permissions`、`follow`、`list`、`remove_recipient`、`set_voice_status`、`trigger_typing_indicator` |
| `commands` | `bulk_overwrite`、`bulk_overwrite_guild`、`create`、`create_guild`、`delete`、`delete_guild`、`edit`、`edit_guild`、`fetch`、`fetch_guild`、`fetch_permissions`、`list`、`list_guild`、`list_permissions` |
| `emojis` | `create`、`create_application`、`delete`、`delete_application`、`edit`、`edit_application`、`fetch`、`fetch_application`、`list`、`list_application` |
| `gateway` | `fetch`、`fetch_bot` |
| `guilds` | `begin_prune`、`edit`、`edit_incident_actions`、`edit_welcome_screen`、`edit_widget`、`fetch`、`fetch_preview`、`fetch_prune_count`、`fetch_vanity_url`、`fetch_welcome_screen`、`fetch_widget`、`fetch_widget_image`、`fetch_widget_settings`、`leave`、`list` |
| `identities` | `delete`、`fetch_profile`、`list`、`list_by_external_id`、`update_profile` |
| `integrations` | `delete`、`list` |
| `invites` | `add_target_user`、`bulk_add_target_users`、`bulk_delete_target_users`、`create`、`delete`、`fetch`、`fetch_target_users_job_status`、`list`、`list_guild`、`list_target_users`、`remove_target_user`、`update_target_users` |
| `lobbies` | `add_member`、`bulk_update_members`、`create`、`create_invite_for_user`、`delete`、`edit`、`fetch`、`remove_member`、`update_message_moderation_metadata` |
| `members` | `add`、`add_role`、`edit`、`edit_current`、`fetch`、`fetch_permissions`、`list`、`remove`、`remove_role`、`search` |
| `messages` | `bulk_delete`、`create`、`crosspost`、`delete`、`edit`、`fetch`、`list`、`list_pins`、`pin`、`purge`、`search`、`unpin` |
| `monetization` | `consume_entitlement`、`create_test_entitlement`、`delete_test_entitlement`、`fetch_entitlement`、`fetch_subscription`、`list_entitlements`、`list_skus`、`list_subscriptions` |
| `onboarding` | `edit`、`fetch` |
| `partner_sdk` | `create_provisional_token`、`exchange_child_token`、`exchange_external_token`、`unmerge_external_account`、`unmerge_provisional_account`、`update_dm_moderation_metadata` |
| `polls` | `end`、`list_answer_voters` |
| `reactions` | `create`、`delete`、`delete_all`、`delete_all_for_emoji`、`delete_own`、`list` |
| `roles` | `create`、`delete`、`edit`、`edit_positions`、`fetch`、`fetch_member_counts`、`fetch_permissions`、`list` |
| `scheduled_events` | `create`、`delete`、`edit`、`fetch`、`list`、`list_users` |
| `soundboard` | `create`、`delete`、`edit`、`fetch`、`list`、`list_default`、`send` |
| `stage_instances` | `create`、`delete`、`edit`、`fetch` |
| `stickers` | `create`、`delete`、`edit`、`fetch`、`fetch_guild`、`fetch_pack`、`list`、`list_packs` |
| `templates` | `create`、`delete`、`edit`、`fetch`、`list`、`sync` |
| `threads` | `add_member`、`fetch_member`、`join`、`leave`、`list_active`、`list_joined_private_archived`、`list_members`、`list_private_archived`、`list_public_archived`、`remove_member`、`start`、`start_from_message`、`start_in_forum` |
| `users` | `edit_current`、`fetch`、`fetch_current` |
| `voice` | `edit_current_state`、`edit_state`、`fetch_current_state`、`fetch_state`、`list_guild_regions`、`list_regions` |
| `webhooks` | `create`、`delete`、`delete_message`、`delete_with_token`、`edit`、`edit_message`、`edit_with_token`、`execute`、`execute_github`、`execute_slack`、`fetch`、`fetch_message`、`fetch_with_token`、`list`、`list_guild` |

每个方法的参数和返回类型见 [REST API 参考](../reference/api/rest.md)。少数操作被有意留给 `bot.http.request`：Discord 已弃用或从未写入文档的端点，以及交互回调和原始回应端点，后两者由[交互上下文](responses.md)负责。

有些分组服务于机器人在服务器中之外的 Discord 功能：`monetization` 和 `application` 用于[变现和 Activity](platform.md)，`lobbies`、`identities` 和 `partner_sdk` 用于 [Social SDK](platform.md#social-sdk)，`application.update_role_connection_metadata` 用于[关联身份组](oauth2.md#linked-roles)。
