---
source: b7efeeb83216fd56b3819a874abdb0bfb6a2a072
description: "bostie 如何与 Discord 同步应用命令：全局和服务器作用域、开发时的测试服务器、命令提及、翻译以及同步错误。"
---

# 注册命令

只有在应用注册了命令之后，Discord 才会显示它。bostie 会替你完成这件事：机器人启动时，它会把你定义的命令与 Discord 上保存的命令进行比较，只有存在差异时才发送。

## 启动时的同步 { #the-sync-at-startup }

`bot.start()` 和 `bot.run()` 会在网关连接之前进行同步。对于每个作用域（全局或某一个服务器），bostie 会获取已注册的命令及其本地化内容，逐字段比较，若有差异，就用一次请求替换该作用域中的全部命令。日志会显示 `commands up to date (global)` 或 `commands synced (global): added [...], removed [...], changed [...]`。

Discord 上存在、但你已不再定义的命令会被移除，除非它属于你从未定义过的类型：没有 `@bot.entry_point` 的机器人会保留 Discord 为其 Activity 创建的入口点命令。

`Bot(auto_sync=False)` 会跳过同步。`await bot.sync_commands()` 会立即替换所有作用域的命令，不论是否有变化，并为每个作用域返回一个 `SyncResult`，其中包含 `added`、`removed` 和 `changed` 的名称，以及 Discord 分配给斜杠命令的 `ids`。

## 作用域 { #scopes }

没有设置 `guilds` 的命令是全局的：它在每个安装了该应用的服务器中都可用，并根据其 `contexts` 的允许范围在私信中可用。`guilds=[...]` 则只在这些服务器中注册它。

开发机器人期间，可以把所有命令都注册到一个测试服务器：

```python
--8<-- "docs/snippets/commands/command_sync.py"
```

`Bot(dev_guilds=[...])` 会把每条没有自己的 `guilds` 的命令注册到这些服务器中，而不是全局注册。入口点命令总是全局的，因此同步会跳过它并发出警告。

### 同一名称用于多个作用域 { #one-name-in-several-scopes }

```python
--8<-- "docs/snippets/commands/guild_variants.py"
```

只要没有哪个服务器同时拥有其中两条，多条命令就可以同名：一条全局的 `/rules` 加上某个服务器专属的 `/rules`，或者每个服务器各有一条 `/rules`。每条命令注册在自己的作用域中，交互会运行在其所在服务器中注册的命令，否则运行全局命令。在那个服务器中，Discord 会同时列出全局命令和服务器命令。在 `dev_guilds` 下，服务器自己的命令会在该服务器中取代全局命令。两条同名命令的服务器有重叠，或者有两条同名的全局命令时，在注册第二条时会引发 `CommandDefinitionError`。用户命令和消息命令遵循同样的规则。

同步只会处理有命令的作用域。去掉 `dev_guilds` 后，那些命令会留在测试服务器中，与新的全局命令并存；`await bot.rest.commands.bulk_overwrite_guild(bot.application_id, guild_id, [])` 可以清除它们。

## 命令可以在哪里使用 { #where-commands-can-be-used }

`contexts` 和 `integration_types` 作用于全局命令。可以在命令、路由器或机器人上设置它们：

- `contexts`：`InteractionContextType.GUILD`、`BOT_DM`（机器人的私信）和 `PRIVATE_CHANNEL`（其他私信和群组私信，适用于用户安装的应用）。
- `integration_types`：`ApplicationIntegrationType.GUILD_INSTALL` 和 `USER_INSTALL`。

`Bot(contexts=[...], integration_types=[...])` 是所有未自行设置这两项的全局命令的默认值。如果哪里都没有设置，bostie 两个字段都不发送，由 Discord 应用其默认值；对于 `integration_types`，默认值就是应用配置的安装场景。移除一个你曾经设置过的设置也算作一次变更，因此下一次同步会让该命令重新使用 Discord 的默认值。

## 命令提及 { #command-mentions }

`await bot.command_mention("roles color set")` 返回 `</roles color set:ID>`，Discord 会把它显示为一个可点击的命令。它使用上一次同步得到的 ID，或者每个作用域向 Discord 查询一次。传入 `guild_id=` 时，在该服务器中注册的命令优先于同名的全局命令。未知的名称会引发 `LookupError`，在机器人启动前调用会引发 `StateError`。如果你已经有命令 ID，`fmt.command(name, command_id)` 可以构建同样的文本。

## 翻译 { #translations }

命令、命令组、子命令、`Option` 或 `Choice` 上的 `name_localizations` 和 `description_localizations` 用于翻译某一段文本。翻译函数则可以一次翻译全部文本：

```python
--8<-- "docs/snippets/commands/command_translations.py"
```

`Bot(translate=function)` 会在同步期间，对每条命令、命令组、子命令和选项的每个名称和描述，以及每个候选项名称，针对每个 `Locale` 各调用一次 `function(text, locale)`。返回 `None` 或原样返回文本时，该语言区域不会添加任何内容；你自己写的本地化优先于该函数。处理函数可以在 `ctx.locale` 中看到用户的语言，在 `ctx.interaction.guild_locale` 中看到服务器的语言。

## 当 Discord 拒绝命令时 { #when-discord-refuses-the-commands }

被 Discord 以 400 拒绝的同步会引发 `CommandSyncError`。它的消息每个问题占一行，指明命令、选项路径和 Discord 的消息，`error.problems` 以 `SyncProblem` 对象的形式保存这些问题。大多数问题会更早被发现：装饰器在运行时就会检查名称、描述和各项限制。
