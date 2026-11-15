---
source: 606da1a817f7cceca11df3edf6dda3b5da72000b
---

# 模态框

![Bostie 填好一张表单并发送出去](../assets/mascot/bostie-modals.webp){ align=right width="180" loading=lazy }

模态框是一个弹出式表单。命令或组件可以用它作为回应，用户的提交会作为一次新的交互到达，由模态框路由处理。

```python
--8<-- "docs/snippets/modals/application_form.py"
```

`ui.modal(custom_id, title, *components)` 接受一个 1 到 45 个字符的标题，以及一到五个组件：`ui.label(...)` 字段和 `ui.text(...)` 段落。`await ctx.modal(...)` 用于打开它；它必须是这次交互的第一次回应，之后任何东西都无法再回应这次交互。

`@bot.on_modal(template)` 路由提交的方式，与 [`on_component`](components.md#routes) 路由点击的方式相同：相同的模板以及 `{name}` 和 `{name:int}` 占位符，相同的 `route.id(...)`，相同的 `ephemeral`、`auto_defer` 和 `checks` 设置。

## 字段 { #fields }

`ui.label(label, component, description=None)` 在一个输入控件上方放置一个最多 45 个字符的标签和一段最多 100 个字符的描述：

| 输入控件 | 构建器 | 读取方式 |
|---|---|---|
| 文本框 | `ui.text_input(custom_id, style=TextInputStyle.SHORT)` | `ctx.text(custom_id)`，一个 `str` |
| 选项选择菜单 | `ui.string_select(custom_id, options)` | `ctx.values(custom_id)` |
| 用户、身份组、二者兼有或频道的选择菜单 | `ui.user_select`、`ui.role_select`、`ui.mentionable_select`、`ui.channel_select` | `ctx.users`、`ctx.members`、`ctx.roles`、`ctx.channels` 或 `ctx.ids` |
| 文件上传 | `ui.file_upload(custom_id)` | `ctx.attachments(custom_id)`，上传的 `Attachment` 对象 |
| 从 2 到 10 项中单选 | `ui.radio_group(custom_id, options)` | `ctx.choice(custom_id)`，所选的值或 `None` |
| 从 1 到 10 项中多选 | `ui.checkbox_group(custom_id, options)` | `ctx.values(custom_id)` |
| 一个“是或否”的复选框 | `ui.checkbox(custom_id, default=False)` | `ctx.checked(custom_id)`，一个 `bool` |

除复选框外的每种输入控件都接受 `required`，默认为 `True`，而 `min_values=0` 需要 `required=False`。`text_input` 接受 `style=TextInputStyle.PARAGRAPH` 以支持多行输入，还接受 `placeholder`、用于预先填写的 `value`、`min_length`（0 到 4000）和 `max_length`（1 到 4000）。`file_upload` 接受 `min_values` 和 `max_values`（最多 10 个文件），以及 `file_types`，例如 `["image", ".pdf"]`。单选和复选组的选项是 `ui.group_option(label, value, description=None, default=False)`；没有设置 `max_values` 的复选组允许用户勾选全部选项。

这些构建器返回 `ui.ModalDict`、`ui.LabelDict`、`ui.TextInputDict`、`ui.FileUploadDict`、`ui.RadioGroupDict`、`ui.CheckboxGroupDict`、`ui.CheckboxDict` 和 `ui.GroupOptionDict`。`ModalContext` 上的读取方法接受输入控件的自定义 ID。模态框中不存在的 ID 会引发 `KeyError`，读取另一种类型的输入控件，例如在复选框上调用 `ctx.text`，会引发 `TypeError`。

## 更新打开模态框的那条消息 { #updating-the-message-that-opened-the-modal }

从组件打开的模态框知道该组件所在的消息，它的提交可以编辑那条消息：

```python
--8<-- "docs/snippets/modals/settings_modal.py"
```

`await ctx.update(...)` 和 `await ctx.defer_update()` 在提交上的作用与在点击上相同。对于从命令打开的模态框，没有可更新的消息，它们会引发 `InteractionResponded`；此时请改用 `respond` 回应。未回应的提交会在 `auto_defer` 秒后被延迟回应，有消息时使用 `defer_update`。

## 在处理函数中等待提交 { #waiting-for-the-submit-inside-a-handler }

```python
--8<-- "docs/snippets/modals/inline_modal.py"
```

`await bot.wait_for_modal(*custom_ids, check=None)` 返回下一次提交的 `ModalContext`，要求其自定义 ID 属于 `custom_ids`（未提供时为任意模态框），并且不会为它运行任何路由。请在自定义 ID 中放入唯一的内容，例如交互 ID，这样两个用户同时运行该命令时会各自得到自己的提交。用户可以不提交就关闭模态框，而 Discord 不会通知机器人，所以请用 `asyncio.timeout()` 限定等待时间。
