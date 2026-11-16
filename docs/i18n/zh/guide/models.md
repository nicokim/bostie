---
source: 33cc84c2aaa4440f3880a38f7ef33c9dff405dec
description: "bostie 如何把 Discord 的 JSON 解码为带类型的模型：部分对象、未知字段和枚举值、原始载荷、Snowflake 时间戳、枚举与标志。"
---

# 模型与枚举

bostie 从 Discord 解码出的所有内容都是模型：`Message`、`Guild`、`Member`、`Interaction` 以及其他大约两百个类，全部可以从 `bostie` 导入。模型是不可变的 `msgspec` 结构体，直接从 JSON 字节解码而来。

```python
--8<-- "docs/snippets/models/inspect_message.py"
```

## 类型遵循 Discord 的文档 { #types-follow-discords-documentation }

每个字段的类型都与 Discord 文档的描述一致：

- Discord 总会发送的字段没有默认值，也绝不会缺失。如果文档说它可以为 `null`，它的类型就是 `T | None`。
- 文档标为可选的字段有默认值：列表为 `()`，标志为空的标志集合，缺失即表示假的布尔值为 `False`，其他情况为 `None`。
- ID 是 `int`，时间戳是带时区的 UTC `datetime` 对象，颜色是 `int`。
- 在 Discord 发送部分对象的地方，字段使用部分模型：`PartialUser`、`PartialMember`、`PartialChannel`、`PartialMessage`、`PartialEmoji`、`PartialApplication`、`PartialIntegration`、`PartialEntitlement`、`UnavailableGuild`。部分模型只包含部分载荷中有的字段，因此访问缺失的字段是类型错误，而不是在运行时得到一个 `None`。

同一种载荷有两种形态的三个地方，各自对应两个类：

- 由 Webhook 发送的消息（包括交互的回应）是 `WebhookMessage`，其 `author` 是 `PartialUser`。其他消息都是 `Message`，其作者是完整的 `User`。两者都接受的字段类型为 `Message | WebhookMessage`。
- 从选项、选择菜单或命令目标解析出的成员是 `ResolvedMember`，它带有其 `user`，但没有 `deaf` 和 `mute`。来自网关或 REST 的 `Member` 两者都有。
- `message.mentions` 中的用户是 `MentionedUser`：一个 `User`，当消息发送在服务器中时，还带有 Discord 附加的 `member`。

交互根据类型分为 `CommandInteraction`、`ComponentInteraction`、`ModalSubmitInteraction` 或 `AutocompleteInteraction`，每种都有带类型的 `data`。

## 变更韧性 { #change-resilience }

Discord 会不经通知地添加字段、枚举值和组件类型，而机器人不能因此崩溃：

- 解码时会忽略 bostie 不认识的字段。
- 枚举会把未知值映射为一个名为 `UNKNOWN_<value>` 的稳定成员，它与该值相等：`MessageType(99).name == "UNKNOWN_99"`。
- 标志会保留 bostie 不认识的位，经过任何运算符（包括 `~`）都不会丢失。
- 未知的组件类型会解码为 `UnknownComponent`，它保留原始 JSON，在你把它放进 `components` 时会原样发回；未知的交互类型会解码为 `UnknownInteraction`，它会到达 `InteractionCreate` 监听器，但不会到达任何命令或路由。
- 嵌套的消息，例如 `message.referenced_message` 或组件交互所在的消息，只有在你读取该属性时才会解码，因此损坏的嵌套消息只会在读取它的地方出错。

完全不符合其类的事件载荷，例如缺少必需字段时，会被记录一次，并且只投递给 `RawEvent` 监听器，[事件](events.md#raw-events-and-events-bostie-does-not-model)中有说明。

## 原始载荷 { #raw-payloads }

从完整载荷解码出的模型会保留该载荷：`model.raw_json` 保存字节，`model.raw` 把它们解码成一个新的 `dict`。嵌套模型，例如消息的作者，其 `raw_json = None`。每个事件也都有 `raw` 和 `raw_json`。借助它们，可以访问 Discord 已发送、但 bostie 尚未建模的任何字段。

模型的 `repr` 不包含 `raw_json` 和机密信息：`token`、`access_token`、`refresh_token` 和 `id_token`。

## 构建与复制模型 { #building-and-copying-models }

模型是不可变的，使用关键字参数构建。`msgspec.structs.replace(model, field=value)` 返回一个修改了部分字段的副本。你发送给 Discord 的模型，例如 `Embed`、`AllowedMentions`、`PollCreateRequest`、`MessageReference`、`ForumTag`、`RecurrenceRule` 和 `AutoModerationAction`，会省略你没有设置的字段，因此解码得到的模型可以原样发回。

## 模型上的辅助属性 { #helpers-on-models }

| 辅助属性 | 值 |
|---|---|
| `user.mention`、`member.mention`、`role.mention`、`channel.mention` | 提及文本，例如 `<@123>` |
| `user.display_name`、`member.display_name` | 全局显示名或用户名；对于成员，优先使用昵称 |
| `message.jump_url` | 指向该消息的链接 |
| `message.author_member` | 消息带有成员信息时，以 `ResolvedMember` 形式表示的作者 |
| `role.permissions`、`overwrite.allow`、`overwrite.deny` | `Permissions` 标志 |
| `member.permissions` | 成员在交互所在频道中的权限（当 Discord 发送了它时） |
| `webhook.url` | Webhook 的 URL（当它带有令牌时） |

[格式化指南](formatting.md)介绍了如何根据 ID 构建提及、时间戳和 CDN URL。

## Snowflake { #snowflakes }

Discord 的 ID 编码了对象的创建时间。每个带有 Snowflake ID 的模型都有 `created_at`，即该时刻的 UTC `datetime`：`message.created_at`、`member.created_at`（账号的创建时间）、`emoji.created_at`（Unicode 表情符号为 `None`）。`bostie.snowflake_time(id)` 对单纯的 ID 做同样的事，`bostie.time_snowflake(when)` 返回某个时刻创建的最小 ID，设置 `high=True` 时返回最大 ID，用于按日期分页。不带时区的 `datetime` 按本地时间解读。

## 枚举 { #enums }

`bostie` 导出了 Discord 的枚举和标志：`ChannelType`、`MessageType`、`Permissions`、`Intents`、`MessageFlags`、`Locale`、`ButtonStyle` 以及[枚举参考](../reference/api/enums.md)中的其他类型。标志用 `|` 组合，用 `in` 检测：`Permissions.SEND_MESSAGES in member_permissions`。`message.type.deletable` 表示 Discord 是否允许删除该类型的消息：群组私信和通话的通知、频道名称和图标的变更通知，以及子区的起始消息都不能删除。`JSONErrorCode` 为 Discord 的错误码命名，与 `HTTPException.code` 报告的一致。
