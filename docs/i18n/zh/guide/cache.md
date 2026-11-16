---
source: 9715861debf2a59f49301a4b9f3800dc2c8262dd
description: "bostie 的缓存从网关保存哪些内容，如何查找服务器、成员和频道并回退到 REST，以及如何计算权限和身份组层级。"
---

# 缓存与权限

![Bostie 把一根骨头丢进盒子里](../assets/mascot/bostie-cache.webp){ align=right width="180" loading=lazy }

`bot.cache` 保存网关告诉机器人的内容，让处理函数无需发请求就能查找数据。它只由事件填充，永远不会由你发起的 REST 调用填充。

```python
--8<-- "docs/snippets/cache/cache_lookups.py"
```

## 缓存哪些内容 { #what-it-keeps }

| 缓存内容 | 默认 | 选项 |
|---|---|---|
| 服务器，以及其身份组、表情符号和贴纸 | 是 | |
| 频道和活跃的子区 | 是 | |
| 语音状态 | 是 | |
| 服务器活动、舞台实例和音效板音效 | 是 | |
| 机器人的用户，以及它在每个服务器中的成员信息 | 是 | |
| 全部成员 | 否 | `members=True`，需要 `Intents.GUILD_MEMBERS` |
| 非离线成员的在线状态 | 否 | `presences=True`，需要 `Intents.GUILD_PRESENCES` |
| 最新的消息 | 否 | `max_messages=N` |
| 最新的私信频道 | 否 | `max_dm_channels=N` |

`Bot(cache=False)` 什么都不缓存，此时其他选项会引发 `ValueError`。缺少相应 Intent 的选项会在创建 `Bot` 时引发 `MissingIntent`。频道按 Discord 发送的原样保存，但 `CHANNEL_UPDATE` 不会保持更新的字段（`last_message_id`、`message_count`、`member_count`、`last_pin_timestamp`）除外，它们为 `None`。

设置 `members=True` 时，机器人连接后 Discord 只会发送大型服务器的部分成员。随后 bostie 会请求其余成员，每个分片一次处理一个服务器，并且只有当分片上的服务器都完整时，该分片才算就绪。`chunk_members=False` 会跳过这一步，缓存会随着成员加入、变更或发言而逐渐填充。被速率限制的请求会在 Discord 给出的延迟之后重试。

## 查找 { #lookups }

| 方法 | 返回 |
|---|---|
| `guild(id)`、`guilds()` | 一个 `Guild`，或全部服务器 |
| `channel(id)`、`guild_channels(guild_id)` | 一个 `Channel`，或某个服务器的频道及其活跃子区 |
| `role(id)`、`guild_roles(guild_id)` | 一个 `Role`，或某个服务器的身份组 |
| `voice_state(guild_id, user_id)`、`guild_voice_states(guild_id)` | 用户在语音中的位置；用户不在任何语音频道时为 `None` |
| `scheduled_event(id)`、`guild_scheduled_events(guild_id)` | 一个 `ScheduledEvent`，或某个服务器的全部服务器活动 |
| `stage_instance(id)`、`guild_stage_instances(guild_id)` | 一个正在进行的 `StageInstance`，或某个服务器的全部舞台实例 |
| `soundboard_sound(sound_id)`、`guild_soundboard_sounds(guild_id)` | 一个 `SoundboardSound`，或某个服务器的全部音效 |
| `me(guild_id)` | 机器人在某个服务器中的 `Member` |
| `member(guild_id, user_id)`、`guild_members(guild_id)` | 成员；除非设置了 `members=True`，否则只有机器人自己 |
| `presence(guild_id, user_id)`、`guild_presences(guild_id)` | 在线状态，需要 `presences=True`；离线时为 `None` |
| `user(id)` | 机器人的用户，或某个已缓存成员或私信频道的用户 |
| `dm_channel(user_id)` | 机器人与某个用户的私信频道（若有缓存） |
| `message(id)` | 最新的 `max_messages` 条消息之一 |
| `permissions(channel_id, member=None)` | 某个成员在某个频道中的权限，省略 `member` 时为机器人的权限 |
| `guild_permissions(guild_id, member=None)` | 同上，范围为整个服务器 |

对于缓存中没有的内容，查找会返回 `None` 或空列表。`bot.cache.clear()` 会清空缓存。更新和删除事件会带上变更之前缓存中的对象，[事件](events.md#old-values-and-the-cache)中有说明。

## 优先缓存，否则 REST { #the-cache-else-rest }

```python
--8<-- "docs/snippets/cache/lookups_with_rest.py"
```

`bot.lookup` 在缓存中有对象时直接返回，否则通过 REST 获取：`user(user_id)`、`member(guild_id, user_id)`、`channel(channel_id)`、`guild(guild_id)`、`role(guild_id, role_id)` 和 `message(channel_id, message_id)`。REST 返回的内容不会被保存：缓存始终只跟随网关，因此再次查找同一个缺失的对象时会再次发请求。REST 的错误会原样抛出，例如对不存在的 ID 抛出 `NotFound`。

## 服务器活动、舞台和音效 { #scheduled-events-stages-and-sounds }

```python
--8<-- "docs/snippets/cache/guild_events.py"
```

它们随每个服务器一起到达，并跟随各自的创建、更新和删除事件更新：服务器活动需要 `Intents.GUILD_SCHEDULED_EVENTS`，音效板音效需要 `Intents.GUILD_EXPRESSIONS`；两者都包含在 `Intents.DEFAULT` 中。对 `bot.request_soundboard_sounds` 的应答会替换某个服务器的音效。它们的更新事件带有 `old`，`SoundboardSoundDelete` 带有被移除的 `sound`。

## 启动 { #startup }

当分片的 `READY` 中列出的每个服务器都以 `GuildCreate` 的形式到达后，或者连续 `guild_ready_timeout` 秒（默认为 2）没有新服务器到达时，该分片就算就绪。`Started` 会等待所有分片。`joined=True` 的 `GuildCreate` 表示机器人刚被添加进的服务器；其他的则是变为可用的服务器。

`large_threshold` 取值 50 到 250，默认为 50，表示成员数超过多少时，Discord 发送服务器信息时不附带其离线成员。

## 权限 { #permissions }

`cache.permissions(channel_id, member)` 遵循 Discord 的规则：服务器所有者和管理员拥有全部权限；否则依次应用 `@everyone` 身份组、成员的身份组，然后是频道中针对 `@everyone`、各身份组和该成员的权限覆盖；被禁言的成员只保留“查看频道”和“阅读消息历史”；没有“查看频道”就什么权限都没有；没有“发送消息”时，依赖它的权限也会失去；在没有“连接”权限的语音频道中，语音相关权限会失去。子区使用其父频道的权限覆盖。当缓存中缺少该频道、该服务器或子区的父频道时，它返回 `None`。

同样的计算也可以不依赖缓存完成：`bostie.permissions.compute_permissions(guild, member, channel=None, *, parent=None)`，其中 `parent` 是子区的父频道。该模块还提供了取自 Discord 文档的若干 `Permissions` 集合：`ALL`、`NONE`、`TEXT`、`VOICE` 和 `STAGE`（各类频道适用的权限）、`ELEVATED`（当服务器要求时，需要版主账号开启双重验证的权限）以及 `TIMED_OUT`（被禁言的成员保留的权限）。

在交互中无需使用缓存：`ctx.interaction.app_permissions` 保存机器人在该频道中的权限，`ctx.member.permissions` 保存用户的权限。

## 身份组层级 { #role-hierarchy }

成员只能踢出、封禁、禁言或编辑最高身份组低于自己的成员，而 Discord 会用成员最高的有颜色身份组的颜色显示其名字。`bostie.permissions` 提供了三个辅助函数，它们接受来自缓存、交互或 REST 的 `Guild` 和成员：

| 辅助函数 | 返回 |
|---|---|
| `top_role(guild, member)` | 成员最高的 `Role`，只有 `@everyone` 时为 `None` |
| `display_colors(guild, member)` | 其最高的有颜色身份组的 `RoleColors`，或 `None` |
| `can_act_on(guild, actor, target)` | `actor` 的等级是否高于 `target`（一个成员或一个身份组）：所有者高于所有人，没有人高于所有者，其他情况下，按位置比较操作者的最高身份组与目标的最高身份组，或与目标身份组本身，与 Discord 管理身份组时的做法一致 |

位置相同的身份组按 ID 排序，较早创建的身份组在前，与 Discord 的排序方式一致。

```python
--8<-- "docs/snippets/cache/role_hierarchy.py"
```
