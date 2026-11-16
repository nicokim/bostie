---
source: 16b527ff6747318f04713be223db608d76440776
description: "用同一套部件构建 Discord 消息：嵌入内容、文件上传、投票、回复、转发、Components V2、正在输入提示和语音消息。"
---

# 消息内容

无论从哪里发送，消息都由同样的部分构成：`ctx.respond`、`ctx.followup`、`ctx.edit` 和 `ctx.update`，`bot.rest.messages.create` 和 `edit`，以及 Webhook 的 `execute`。每个部分都是一个关键字参数，省略的部分不会被发送。

| 部分 | 类型 | 说明 |
|---|---|---|
| `content` | `str` | `respond`、`followup`、`messages.create` 和 `execute` 的第一个位置参数 |
| `embeds` | `Embed` 对象 | 最多 10 个富嵌入内容 |
| `components` | `ui` 构建器 | 按钮、选择菜单和布局组件；参见[组件](components.md) |
| `files` | `File` 对象 | 上传的文件；见下文 |
| `allowed_mentions` | `AllowedMentions` | 哪些提及可以发出通知；参见[允许的提及](responses.md#allowed-mentions) |
| `flags` | `MessageFlags` | 例如 `SUPPRESS_EMBEDS` 和 `SUPPRESS_NOTIFICATIONS` |
| `tts` | `bool` | 为打开该频道的人朗读内容 |
| `poll` | `PollCreateRequest` | 一个投票；见下文 |
| `ephemeral` | `bool` | 仅用于交互的回应 |

`bot.rest.messages.create` 还接受 `reply_to`、`message_reference`、`sticker_ids`、`nonce`、`enforce_nonce` 和 `shared_client_theme`，以及和[交互回应](responses.md#messages-that-delete-themselves)一样的 `delete_after`。

## 嵌入内容和文件 { #embeds-and-files }

```python
--8<-- "docs/snippets/responses/rich_messages.py"
```

`Embed` 及其组成部分（`EmbedField`、`EmbedFooter`、`EmbedAuthor`、`EmbedMedia`）都是使用关键字参数构造的不可变模型；按 Discord JSON 结构写的普通 `dict` 也可以。`color` 接受一个 `int`，例如 `0x5865F2`，也可以用 [`bostie.color`](formatting.md#colors) 中的辅助函数构建。

`File(name, data)` 以 `name` 为文件名上传 `data`。`description` 是它的替代文本，`spoiler=True` 会将其模糊处理，`title` 设置 Discord 显示的标题。嵌入内容或组件可以通过 `attachment://name` 引用上传的文件，就像上面的图表那样。bostie 不检查文件大小：Discord 的上传上限取决于服务器的助力等级和用户的 Nitro。

## 投票 { #polls }

`PollCreateRequest` 接受一个 `question` 和若干 `answers`，二者都是 `PollMedia`，带有 `text`，答案还可以有一个可选的 `emoji`。`duration` 以小时为单位，省略时为 24，`allow_multiselect=True` 允许用户选择多个答案。`bot.rest.polls.end(channel_id, message_id)` 提前结束投票，`bot.rest.polls.list_answer_voters(...)` 列出选择了某个答案的人，`PollVoteAdd` 和 `PollVoteRemove` [事件](events.md)会实时报告投票。

## 编辑与附件 { #editing-and-attachments }

编辑以关键字参数接受各个部分，外加 `attachments`。省略的部分保持不变，`None` 则会清除它：`ctx.edit(embeds=None)` 会移除嵌入内容并保留文字内容。当一次编辑传入了 `files` 或 `attachments` 时，消息只保留 `attachments` 中列出的附件（以 `Attachment` 对象或 ID 给出），再加上新文件。

## Components V2 { #components-v2 }

如果消息的 `components` 中含有布局组件（`ui.text`、`ui.section`、`ui.container`、`ui.media_gallery`、`ui.file`、`ui.separator` 或 `ui.thumbnail`），它就是一条 Components V2 消息：bostie 会设置 `IS_COMPONENTS_V2` 标志，组件构成整条消息。这样的消息不能带有 `content`、`embeds`、贴纸或投票，传入它们时 bostie 会引发 `ValueError`；请把文字放进 `ui.text`。仅在 `ui.row` 中放按钮和选择菜单，则同样可用于带文字内容的经典消息。

## 回复 { #replies }

```python
--8<-- "docs/snippets/responses/replies.py"
```

`reply_to` 接受同一频道中某条消息的 ID。要转发消息，请改为传入 `message_reference=MessageReference(type=MessageReferenceType.FORWARD, message_id=..., channel_id=...)`；两者不能同时使用。在服务器中读取 `message.content` 需要特权 Message Content Intent，[网关指南](gateway.md#intents)中有详细说明。

## 正在输入提示 { #typing-indicator }

```python
--8<-- "docs/snippets/responses/typing_indicator.py"
```

`async with ctx.typing():` 会在代码块运行期间，在交互所在频道显示机器人正在输入。bostie 在代码块开始时发送该提示，之后每 9 秒再发送一次，因为 Discord 只显示 10 秒；代码块结束时（包括因异常结束）停止发送。`await ctx.typing()` 只显示一次。在交互之外，`bot.rest.channels.trigger_typing_indicator(channel_id)` 的作用相同，可以直接 await，也可以用在 `async with` 中。机器人在该频道发送消息后，提示会立即消失。

## 语音消息 { #voice-messages }

```python
--8<-- "docs/snippets/responses/voice_memo.py"
```

`await voice.message(audio)` 读取一个文件路径或字节数据，测量时长，计算 Discord 绘制的波形，并返回一个可直接发送的 `File`。文件扩展名决定格式；Discord 客户端录制的是 48 kHz 单声道 Ogg Opus。语音消息只能包含这一个文件，不能有其他任何东西：不能有文字内容、嵌入内容、组件、投票或贴纸。bostie 会设置 `IS_VOICE_MESSAGE` 标志，如果消息中还有其他内容则引发 `ValueError`。你自己构造的 `File` 只要带有 `duration_secs` 和 `waveform`（Discord 绘制的 8 位采样值），就会成为语音消息。
