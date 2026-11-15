---
source: d0cf09522c13a747c853db29f2b054fe520abbea
---

# 组件

![Bostie 按下一个大按钮](../assets/mascot/bostie-components.webp){ align=right width="180" loading=lazy }

组件是消息中的按钮、选择菜单和布局块。`bostie.ui` 会把它们构建成 Discord JSON 结构的普通字典，并在构建时检查 Discord 的各项限制。用 `@bot.on_component` 注册的处理函数负责回应对它们的使用。

`bostie.ui` 是构建组件的方式。组件模型，例如 `bostie.Button` 或 `bostie.ActionRow`，是解码后的消息中 `components` 所保存的内容；`components=` 也接受它们，因此可以把消息的组件原样发回，或用 `msgspec.structs.replace` 修改后再发，无需重新构建，但它们不会检查 Discord 的限制。

## 布局 { #layout }

```python
--8<-- "docs/snippets/components/layout.py"
```

这些是 Components V2：组件构成整条消息，用 `ui.text` 代替 `content`，[消息内容](messages.md#components-v2)中有说明。

| 构建器 | 显示内容 |
|---|---|
| `ui.text(markdown)` | 文本，支持 Discord 的 Markdown |
| `ui.section(*texts, accessory=...)` | 一到三段文本，旁边配一个按钮或缩略图 |
| `ui.thumbnail(url, description=None, spoiler=False)` | 一张小图，只能作为 section 的附属组件 |
| `ui.media_gallery(*items)` | 一到十张图片或视频，每项是一个 `ui.gallery_item(url, description=None, spoiler=False)` |
| `ui.file(filename, spoiler=False)` | 随消息以该文件名上传的文件 |
| `ui.separator(divider=True, spacing=1)` | 组件之间的间距，`spacing=2` 为大间距，除非 `divider=False`，否则带一条分隔线 |
| `ui.container(*components, accent_color=None, spoiler=False)` | 包裹行、文本、section、图库、文件和分隔线的方框，带一条彩色边 |
| `ui.row(*components)` | 最多五个按钮，或一个选择菜单 |

URL 可以写成 `attachment://name`，以显示随消息上传的文件。每个构建器都接受 `id=`，作为可选的数字组件 ID，`ui.raw(dict)` 可以原样传入一个 `ui` 尚未提供构建器的组件。项目过多、标签过长，或在带按钮的行中放入选择菜单，都会在构建时引发 `ValueError`。每个构建器都返回一个带类型的字典，例如 `ui.ButtonDict`、`ui.RowDict`、`ui.TextDict`、`ui.SectionDict`、`ui.ThumbnailDict`、`ui.MediaGalleryDict`、`ui.GalleryItemDict`、`ui.FileDict`、`ui.SeparatorDict`、`ui.ContainerDict`、`ui.StringSelectDict`、`ui.SelectOptionDict`、`ui.EntitySelectDict` 和 `ui.SelectDefaultDict`，`ui.raw` 则返回 `ui.RawComponent`；可以用它们来标注你自己的辅助函数，就像下面的 `ballot` 那样。

## 按钮 { #buttons }

- `ui.button(label, custom_id=..., style=ButtonStyle.SECONDARY)` 被点击时会把 `custom_id` 发送给机器人。`style` 可以是 `PRIMARY`、`SECONDARY`、`SUCCESS` 或 `DANGER`。
- `ui.link_button(label, url)` 打开一个 URL，永远不会触达机器人。
- `ui.premium_button(sku_id)` 用于出售应用的某个 SKU，永远不会触达机器人。

按钮接受 `emoji=`：一个 Unicode 表情符号、一个按 Discord 显示格式书写的自定义表情符号（`<:name:id>`，动态表情为 `<a:name:id>`，与 `fmt.emoji` 生成的一致），或一个 `Emoji` 或 `PartialEmoji` 模型。`PartialEmoji.parse(text)` 可以读取这样的字符串。按钮还接受 `disabled=True`。标签最多 80 个字符，自定义 ID 为 1 到 100 个字符。

## 路由 { #routes }

路由是一个处理函数，负责处理所有符合某个模板的自定义 ID：

```python
--8<-- "docs/snippets/components/routes.py"
```

`"vote:{side:int}:{topic}"` 可以匹配 `vote:0:Lunch at noon`。`{name}` 捕获一个或多个不含换行的字符，`{name:int}` 捕获一个整数，包括负数。这些值以关键字参数的形式传给处理函数，`int` 类型会被转换。模板中两个占位符之间必须有字面文本，没有占位符的模板会精确匹配其自定义 ID。

`vote.id(side=0, topic=topic)` 直接从路由本身构建自定义 ID，因此模板只需写一次。如果 ID 会超过 Discord 的 100 个字符，或无法解析回相同的值，它会引发 `ValueError`。

状态保存在自定义 ID 中，而不是 Python 对象中，因此只要路由已注册，上周发出的按钮在重启后依然有效。`@bot.on_component` 和命令一样接受 `ephemeral`、`auto_defer` 和 `checks`，调用路由本身，如 `await vote(ctx, side=0, topic="x")`，就会调用其处理函数。

没有任何路由匹配的组件使用不会得到回应：Discord 会显示“This interaction failed”，bostie 会记录一条警告，除非你监听了 `InteractionCreate`。

## 回应组件 { #answering-a-component }

`ComponentContext` 拥有命令上下文的所有回应方法，另外还有：

- `await ctx.update(**parts)` 编辑组件所在的消息。省略的部分保持不变，`None` 会清除某个部分。
- `await ctx.defer_update()` 确认这次使用但不显示任何回应；之后 15 分钟内可以用 `update` 编辑消息。
- `await ctx.modal(ui.modal(...))` 打开一个[模态框](modals.md)。

`respond` 会发送一条新消息，与命令中相同。超过 `auto_defer` 秒仍未回应的使用会用 `defer_update` 延迟回应。

## 选择菜单 { #select-menus }

```python
--8<-- "docs/snippets/components/selects.py"
```

| 构建器 | 用户选择的是 | 用以下方式读取所选内容 |
|---|---|---|
| `ui.string_select(custom_id, options)` | 用 `ui.select_option(label, value)` 构建的选项 | `ctx.values`，即所选的值 |
| `ui.user_select(custom_id)` | 用户 | `ctx.users`，在服务器中还有 `ctx.members` |
| `ui.role_select(custom_id)` | 身份组 | `ctx.roles` |
| `ui.mentionable_select(custom_id)` | 用户和身份组 | `ctx.users`、`ctx.members`、`ctx.roles` |
| `ui.channel_select(custom_id, channel_types=...)` | 频道，仅限给定类型 | `ctx.channels` |

`ctx.ids` 保存任何实体选择菜单中所选的 ID。读取错误的种类，例如在用户选择菜单上读取 `ctx.values`，会引发 `TypeError`。

每个选择菜单都接受 `placeholder`（最多 150 个字符）、`min_values` 和 `max_values`（默认为 1，最多 25）以及 `disabled`。字符串选择菜单有 1 到 25 个选项；`select_option` 接受 `description`、与按钮相同的 `emoji`，以及 `default=True` 以将其显示为已选中。实体选择菜单接受 `defaults`，即显示为已选中的 ID，可提及对象选择菜单接受 `default_users` 和 `default_roles`。`required` 只在[模态框](modals.md)中有意义。

## 分页 { #pages }

`ui.pager(route, page, count)` 为带有 `{page:int}` 占位符的路由返回一行三个按钮：上一页、一个已禁用的 `page / count` 标签，以及下一页。第一页是 0，额外的关键字参数用于填充路由的其他占位符。因为页码保存在自定义 ID 中，所以分页在重启后依然有效。[组件示例](../examples.md#components)中的 `/tour` 命令就用到了它。

## 在处理函数中等待点击 { #waiting-for-a-click-inside-a-handler }

命令可以在不使用路由的情况下等待某个组件的一次使用：

```python
--8<-- "docs/snippets/components/confirm.py"
```

`await bot.wait_for_component(*custom_ids, check=None)` 返回下一次使用的 `ComponentContext`，要求其自定义 ID 属于 `custom_ids`（未提供时为任意组件），并且 `check` 对其返回真值。被它返回的使用不会运行任何路由。它会一直等待下去，所以请用 `asyncio.timeout()` 限定等待时间；`check` 抛出的异常会在这里抛出。它接受 `ephemeral` 和 `auto_defer`，作用于它返回的上下文。
