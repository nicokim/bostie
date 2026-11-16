---
source: 620651abbb35486bdf47aba0669128b8bb1d0ad6
description: "用 Python 类型提示定义 Discord 斜杠命令：选项类型、候选项、取值范围、自动补全、转换器、子命令组以及 Discord 的限制。"
---

# 斜杠命令

![Bostie 在对话气泡里写下一条斜杠命令](../assets/mascot/bostie-commands.webp){ align=right width="180" loading=lazy }

斜杠命令就是一个用 `@bot.command` 装饰的异步函数。它的第一个参数接收 [`Context`](responses.md)；之后的每个参数都是命令的一个选项。

```python
--8<-- "docs/snippets/commands/option_types.py"
```

这条命令是 `/report`。命令名就是函数名，描述是文档字符串的第一行；也可以用 `@bot.command(name=..., description=...)` 来指定，两者都没有的命令会以 `…` 作为描述。`@bot.command` 和 `@bot.command(...)` 两种写法都可以，在机器人上或在[路由器](routers.md)上都能用。

## 选项类型 { #option-types }

参数的类型提示决定选项类型，处理函数收到的是已经转换好的值：

| 类型提示 | Discord 选项 | 处理函数收到的值 |
|---|---|---|
| `str` | 字符串 | 文本 |
| `int` | 整数 | 一个 `int` |
| `float` | 数字 | 一个 `float`，即使用户输入的是整数 |
| `bool` | 布尔值 | 一个 `bool` |
| `User` | 用户 | 该 `User` |
| `ResolvedMember` | 用户 | 成员，带有其 `user`；仅限服务器内 |
| `Role` | 身份组 | 该 `Role` |
| `PartialChannel` | 频道 | 交互解析出的频道 |
| `Attachment` | 附件 | 上传文件的 `Attachment` |
| `User \| Role` 或 `ResolvedMember \| Role` | 可提及对象 | 用户所选的那一个 |
| `Literal[...]` 或某个 `Enum` | 带候选项的字符串、整数或数字 | 字面量或枚举成员 |

表中未列出的类型提示会在装饰器运行时引发 `CommandDefinitionError`。频道选项的类型提示是 `PartialChannel` 而不是 `Channel`，成员则是 `ResolvedMember` 而不是 `Member`：交互中携带的就是这些部分对象，错误信息里也会说明这一点。

## 必填与可选 { #required-and-optional }

没有默认值的选项是必填的。设置默认值，或在类型提示中加上 `| None`，就会让它变成可选；用户没有填写的可选选项会得到默认值，或者 `None`。Discord 要求必填选项排在前面，无论参数顺序如何，bostie 在构建命令时都会按这个顺序排好。

## 描述 { #descriptions }

`Annotated` 中的字符串就是选项的描述：`Annotated[str, "What happened"]`。也可以把描述写在文档字符串里：

```python
--8<-- "docs/snippets/commands/docstring_options.py"
```

bostie 能读取上面这样的 `Args:` 小节，也能读取用短横线作下划线的 `Parameters` 小节；一条描述可以跨越多行。`Annotated` 中的字符串优先于 `Option(description=)`，而后者又优先于文档字符串。哪里都没有描述的选项会得到 `…`。

## 取值范围与选项设置 { #ranges-and-option-settings }

`Range[int, 1, 10]` 是一个取值 1 到 10（含两端）的 `int` 选项。`Range` 也接受 `float`，对于 `str`，边界指的是长度：`Range[str, 1, 2000]`。`Annotated` 中的 `Option(...)` 用于设置其他所有内容，并且优先于类型提示所隐含的设置：

```python
--8<-- "docs/snippets/commands/option_settings.py"
```

| `Option` 字段 | 适用于 | 含义 |
|---|---|---|
| `name` | 全部 | 选项在 Discord 上的名称；参数保留它的 Python 名称 |
| `description` | 全部 | 描述，在 `Annotated` 中没有字符串时使用 |
| `name_localizations`、`description_localizations` | 全部 | 按语言区域提供的文本，例如 `{"fr": "salon"}` |
| `min`、`max` | 整数、数字 | 闭区间边界，在 ±2⁵³ 以内 |
| `min_length`、`max_length` | 字符串 | 长度边界，分别为 0 到 6000 和 1 到 6000 |
| `channel_types` | 频道 | 用户可以选择的频道类型 |
| `file_types` | 附件 | 最多 10 个，取 `image`、`video`、`audio` 或 `.pdf` 这样的扩展名 |
| `choices` | 字符串、整数、数字 | 固定的候选项；见下文 |
| `autocomplete` | 字符串、整数、数字 | 在用户输入时提供建议值的函数 |
| `extra` | 全部 | 合并到选项 JSON 中的原始字段，用于 bostie 尚未建模的字段 |

给不适用的选项类型设置字段会引发 `CommandDefinitionError`。

## 候选项 { #choices }

`Literal` 或 `Enum` 提供一个固定列表。`Literal` 候选项以其值命名；`Enum` 候选项以成员名命名，发送的是成员的值。`Option(choices=[...])` 可以重命名候选项或为其提供本地化，只要它以相同顺序列出相同的值即可：

```python
--8<-- "docs/snippets/commands/option_choices.py"
```

`/order` 的处理函数在 `drink` 上得到 `"tea"`、`"coffee"` 或 `"water"`，在 `size` 上得到一个 `Size` 成员，在 `milk` 上得到所选 `Choice` 的值。对于普通的 `str`、`int` 或 `float` 类型提示，仅凭 `Option(choices=[...])` 就能定义候选项；每一项可以是 `Choice(name, value)`，也可以是一个直接的值，以其自身命名。候选项的值必须与选项类型一致，`Literal` 或 `Enum` 的值必须全是 `str`、全是 `int` 或全是 `float`。一个选项最多 25 个候选项，候选项名称为 1 到 100 个字符，字符串值最多 100 个字符。

## 自动补全 { #autocomplete }

`Option(autocomplete=function)` 会在用户输入时向 `function` 索取建议。它接收一个 `AutocompleteContext` 和用户目前已输入的文本，返回值或 `Choice` 对象：

```python
--8<-- "docs/snippets/commands/autocomplete_options.py"
```

`ctx.options` 按选项名保存目前已填写的选项，能转换的会按处理函数所得的形式转换好；正在输入的那个选项保持为文本。bostie 会发送前 25 条建议，截断超过 100 个字符的名称，并丢弃名称为空、值类型不对或字符串值超过 100 个字符的建议，每种情况都会输出一条警告。一个选项不能同时拥有 `choices` 和 `autocomplete`。如果该函数抛出异常，错误会交给[错误钩子](checks.md#error-hooks)，Discord 则收到一个空列表。

## 转换器 { #converters }

`Annotated[T, Convert(function)]` 会在处理函数运行前把选项的值转换成 `T`。`function(ctx, value)` 可以是异步函数，它的 `value` 参数的类型提示决定 Discord 选项类型，包括 `Range` 和 `Annotated` 中的描述：

```python
--8<-- "docs/snippets/commands/convert_options.py"
```

转换器只对用户提供了的选项运行；未填写的可选选项会直接得到未经转换的默认值。转换器抛出的异常和处理函数抛出的异常一样，会交给错误钩子。

## 子命令与命令组 { #subcommands-and-groups }

`bot.group(name, description)` 注册一个由子命令组成的命令，`group.group(...)` 在其中再添加一个子命令组。命令组只能嵌套一层，一个命令组最多包含 25 项：

```python
--8<-- "docs/snippets/commands/command_groups.py"
```

上面定义了 `/roles give` 和 `/roles color set`。`@group.command(name=...)` 可以给子命令起一个与函数不同的名字。`ctx.command_name` 是实际运行的完整名称，例如 `"roles color set"`，命令对象的 `command.qualified_name` 也一样。

顶层命令组接受除 `name`、`ephemeral` 和 `auto_defer` 以外的命令设置，并对其下所有子命令生效：在 Discord 中，`default_permissions`、`nsfw`、`guilds`、`contexts` 和 `integration_types` 只存在于顶层命令上。嵌套的命令组接受两个本地化设置、`checks` 和 `extra`。子命令接受 `name`、`description`、两个本地化设置、`ephemeral`、`auto_defer`、`checks` 和 `extra`。命令组的检查先于其子命令的检查运行。

## 命令设置 { #command-settings }

```python
--8<-- "docs/snippets/commands/command_settings.py"
```

| 设置 | 含义 |
|---|---|
| `name`、`description` | 名称和描述，代替函数自带的 |
| `name_localizations`、`description_localizations` | 按语言区域提供的文本；翻译函数参见[注册命令](registration.md#translations) |
| `ephemeral` | 回应仅自己可见，除非某次调用另有指定 |
| `auto_defer` | 多少秒后对尚未回应的使用自动延迟回应；`None` 表示关闭（[回应交互](responses.md#deferring)） |
| `default_permissions` | 成员看到并使用该命令所需的权限；服务器管理员可以更改谁能使用 |
| `nsfw` | 只在年龄限制频道中显示 |
| `guilds` | 要注册该命令的服务器 ID，代替全局注册 |
| `contexts` | 全局命令可以在哪里使用：服务器、机器人的私信、私密频道 |
| `integration_types` | 哪些安装方式带有该命令：服务器安装、用户安装 |
| `checks` | 在处理函数之前运行的检查（[检查](checks.md)） |
| `extra` | 合并到命令 JSON 中的原始字段 |

`ephemeral`、`auto_defer`、`default_permissions`、`nsfw`、`contexts` 和 `integration_types` 也可以为某个[路由器](routers.md#defaults)的所有命令统一设置，而 `contexts`、`integration_types` 和 `auto_defer` 还可以为机器人的所有命令统一设置。

## 限制 { #limits }

装饰器会检查 Discord 会拒绝的内容，并引发 `CommandDefinitionError`，指明出错的函数和参数：

- 名称为 1 到 32 个字符，由字母、数字、`-` 和 `_` 组成，不能有大写字母。用户命令和消息命令的名称还可以包含大写字母和空格。
- 描述为 1 到 100 个字符。
- 一个命令最多 25 个选项，一个选项最多 25 个候选项。
- 不接受 `*args`、`**kwargs`、仅限位置的选项以及没有类型提示的选项。

当一个命令的名称、描述和候选项值加起来超过 Discord 允许的 8000 个字符时，会给出一条警告，而不是错误。当一个作用域中有超过 100 条斜杠命令、15 条用户命令或 15 条消息命令时，同步时会发出警告。

## 下一步 { #next }

- [用户命令、消息命令和入口点命令](context-menus.md)
- 向 Discord [注册命令](registration.md)，在单个服务器中或全局注册，以及翻译命令
- [回应交互](responses.md)
