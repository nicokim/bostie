---
source: b1ea93c8ecd81e5df88e6e3728f48038a3213951
description: "回应 Discord 交互：回应、延迟回应、编辑、后续消息、仅自己可见的消息、自动延迟回应、3 秒与 15 分钟的限制以及允许的提及。"
---

# 回应交互

每条斜杠命令、上下文菜单命令、按钮、选择菜单和模态框提交都是一次交互，Discord 会等待应用对它作出回应。命令处理函数得到一个 `Context`，组件处理函数得到 `ComponentContext`，模态框处理函数得到 `ModalContext`；它们共享下面介绍的回应方法。

## 上下文里有什么 { #what-a-context-knows }

| 属性 | 值 |
|---|---|
| `ctx.user` | 触发交互的 `User`，无论在服务器还是私信中 |
| `ctx.member` | 在服务器中是其 `Member`，否则为 `None` |
| `ctx.guild_id`、`ctx.channel_id` | 发生的位置；在私信中 `guild_id` 为 `None` |
| `ctx.channel` | 交互携带的 `PartialChannel` |
| `ctx.locale` | 用户客户端的语言 |
| `ctx.interaction` | 完整的交互模型，用于其他所有信息，例如 `app_permissions` 或 `guild_locale` |
| `ctx.bot` | 机器人 |
| `ctx.voice` | 该服务器和该用户的[语音](../voice/index.md) |
| `ctx.command_name` | 在 `Context` 上，是带子命令的完整命令名，例如 `"roles color set"` |

## 回应、编辑、后续消息 { #answer-edit-follow-up }

```python
--8<-- "docs/snippets/responses/answer_flow.py"
```

`ctx.respond(...)` 发送第一次回应。再次调用时，它会改为发送一条后续消息，因此处理函数可以按需多次调用它。它返回 Discord 创建的消息，一个 `WebhookMessage`。

- `ctx.edit(**parts)` 编辑第一次回应，`ctx.edit(message_id, **parts)` 编辑某条后续消息。
- `ctx.followup(...)` 总是发送一条新消息，唯一的例外是紧跟在 `defer()` 之后，此时它会填充被延迟的那次回应。
- `ctx.fetch()` 和 `ctx.fetch(message_id)` 返回 Discord 上当前的回应或后续消息。
- `ctx.delete()` 和 `ctx.delete(message_id)` 删除它们。

`respond` 和 `followup` 接受 `content` 以及[消息内容](messages.md)中列出的各部分：`embeds`、`components`、`files`、`allowed_mentions`、`flags`、`tts`、`poll`，再加上 `ephemeral`。编辑以关键字参数接受同样的部分：省略的部分保持不变，传入 `None` 则清除该部分。

## 自动删除的消息 { #messages-that-delete-themselves }

```python
--8<-- "docs/snippets/responses/short_lived.py"
```

`respond`、`followup` 和 `bot.rest.messages.create` 上的 `delete_after=` 会在指定秒数之后删除所发送的消息。机器人在后台执行删除，并在关闭时取消它，因此计划在机器人停止之后才删除的消息会保留下来。交互的消息通过其令牌删除，这对仅自己可见的消息同样有效，但令牌在交互发生 15 分钟后失效；计划在那之后的删除会失败，并记录一条警告。已经被别人删除的消息会被悄悄跳过。

## 仅自己可见的回应 { #ephemeral-answers }

在某次调用上传入 `ephemeral=True`，会让那条消息仅用户自己可见。在命令、组件路由、模态框路由或路由器上设置 `ephemeral=True`，会让它成为这些对象所有回应的默认行为，而某次调用仍然可以传入 `ephemeral=False`。已有的消息不能在仅自己可见和公开之间切换：要求这样做的编辑会记录一次警告，并保持消息原样。

## 延迟回应 { #deferring }

第一次回应必须在 3 秒内送达 Discord。`await ctx.defer()` 会在原处显示“thinking…”，之后处理函数有 15 分钟时间调用 `respond`；`defer(ephemeral=True)` 会让随后的回应仅自己可见。

```python
--8<-- "docs/snippets/responses/deferring.py"
```

bostie 也会自动延迟回应：交互到达 `auto_defer` 秒后仍未回应的，会被延迟回应，默认是 2.5 秒。从消息上打开的组件或模态框会用 `defer_update` 延迟，它会保持消息原样，直到 `update` 编辑它。`auto_defer` 可以设置在机器人、路由器、命令或路由上，设为 `None` 则在该处关闭。计时从网关送达交互时开始，因此被阻塞的事件循环会推迟处理函数，却不会推迟截止时间；当处理函数晚于一秒以上才开始时，bostie 会记录一条警告。

## 模态框与 Activity { #modals-and-activities }

`await ctx.modal(ui.modal(...))` 以一个弹出式表单作为回应；参见[模态框](modals.md)。`await ctx.launch_activity()` 打开应用的 Activity，并返回其实例 ID。两者都必须是第一次回应，并且在模态框之后，任何东西都无法再回应这次交互：模态框的提交是一次新的交互。

## 规则，以及违反规则时的错误 { #the-rules-and-the-errors-for-breaking-them }

每次调用都会先检查交互的状态：

| 调用 | 尚未回应 | `defer()` 之后 | `defer_update()` 之后 | 已回应之后 |
|---|---|---|---|---|
| `respond` | 回应 | 填充回应 | 发送后续消息 | 发送后续消息 |
| `defer` | 延迟回应 | 无操作 | 无操作 | `InteractionResponded` |
| `edit`、`delete` | `InteractionNotAnswered` | 编辑、删除回应 | 编辑、删除消息 | 编辑、删除回应 |
| `followup` | `InteractionNotAnswered` | 填充回应 | 发送后续消息 | 发送后续消息 |
| `update` | 编辑消息 | `InteractionResponded` | 编辑消息 | `InteractionResponded` |
| `modal`、`launch_activity` | 回应 | `InteractionResponded` | `InteractionResponded` | `InteractionResponded` |

`update` 和 `defer_update` 只存在于组件和模态框的上下文中。在模态框之后进行的任何调用都会引发 `InteractionResponded`。

交互令牌的有效期是 15 分钟。在那之后，或者在 Discord 表示令牌无效之后，任何调用都会引发 `InteractionExpired`，不会发送任何内容。

处理函数没有回应就返回时，用户会看到“The application did not respond”，bostie 会记录一条指明该处理函数的警告。处理函数延迟了回应却始终没有回应时，“thinking…”会一直显示到令牌过期，同样会有一条警告。处理函数在回应之前抛出异常时，如果没有[错误钩子](checks.md#error-hooks)作出回应，bostie 会回应“Something went wrong.”；这条回应仅自己可见，除非处理函数此前已延迟回应。

对于只安装到用户账号的应用，Discord 允许每次交互发送 5 条后续消息，bostie 会在第 6 条时发出警告。

## 允许的提及 { #allowed-mentions }

默认情况下，回应可以通知用户和身份组，但不能通知 `@everyone` 或 `@here`：`Bot(allowed_mentions=...)` 的默认值是 `AllowedMentions(parse=("users", "roles"))`。随消息传入的 `allowed_mentions` 只会改变它所设置的那几类提及，因此 `AllowedMentions(users=(123,))` 只限制对用户的通知，身份组的设置保持不变。`AllowedMentions(parse=())` 让所有提及都不发出通知，`replied_user=False` 则让回复不通知被回复消息的作者。
