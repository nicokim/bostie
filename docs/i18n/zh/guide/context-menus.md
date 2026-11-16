---
source: db591175a7dec7d094226c92a4855de730089f1d
description: "添加用户和消息上下文菜单命令，以及启动 Activity 的入口点命令，包括带类型的目标和每种命令可用的设置。"
---

# 用户命令、消息命令和入口点命令

除了斜杠命令，Discord 还有从用户或消息的上下文菜单中运行的命令，以及用于打开应用 Activity 的入口点命令。

## 用户命令和消息命令 { #user-and-message-commands }

```python
--8<-- "docs/snippets/commands/context_menus.py"
```

`@bot.user_command(name)` 和 `@bot.message_command(name)` 注册一个接收上下文和目标对象的函数。名称就是菜单中显示的文字：1 到 32 个字符，与斜杠命令名不同，它可以包含大写字母和空格。这类命令没有描述，也没有选项。

目标的类型提示决定处理函数收到什么：

| 命令 | 类型提示 | 处理函数收到的值 |
|---|---|---|
| 用户 | `User` | 用户 |
| 用户 | `ResolvedMember` | 成员，带有其 `user`；仅限服务器内 |
| 用户 | `User \| ResolvedMember` | 在服务器内是成员，在其他地方是用户 |
| 消息 | `Message` | 由用户或机器人发送的消息 |
| 消息 | `WebhookMessage` | 由 Webhook 发送的消息，包括交互的回应 |
| 消息 | `Message \| WebhookMessage` | 二者之一 |

无法解析为所注类型的目标会引发 `OptionConversionError`，并交给[错误钩子](checks.md#error-hooks)处理。除非命令只对其中一种有意义，否则请把消息目标的类型提示写成 `Message | WebhookMessage`。

它们接受斜杠命令的设置，但 `description`、`description_localizations` 和选项设置除外：即 `name_localizations`、`ephemeral`、`auto_defer`、`default_permissions`、`nsfw`、`guilds`、`contexts`、`integration_types`、`checks` 和 `extra`。Discord 允许每个作用域有 15 条用户命令和 15 条消息命令，超出时同步会发出警告。

## 入口点命令 { #the-entry-point-command }

带有 Activity 的应用有一条入口点命令，显示在应用启动器中。`@bot.entry_point(name)` 用一个只接收上下文的函数来注册它：

```python
--8<-- "docs/snippets/commands/entry_point.py"
```

处理函数的回应方式和任何命令一样：`ctx.launch_activity()` 打开 Activity 并返回其实例 ID，`ctx.respond(...)` 则改为用一条消息回应。如果应用没有在 Developer Portal 中启用 Activity，Discord 会拒绝这条命令。

入口点命令总是全局的。设置了 `dev_guilds` 时，同步会跳过它并发出警告。如果 Discord 上已经有一个名称不同的默认入口点，同步会沿用那条命令的 ID，于是 Discord 会替换它，而不是两条都保留。`launch_activity()` 在组件和模态框的上下文中也可用，但它必须是第一次回应。

`entry_point` 接受 `description`、`name_localizations`、`description_localizations`、`ephemeral`、`auto_defer`、`default_permissions`、`nsfw`、`contexts`、`integration_types`、`checks` 和 `extra`。Activity 方面的其余内容请参见 [Activity、变现和 Social SDK](platform.md#activities)。
