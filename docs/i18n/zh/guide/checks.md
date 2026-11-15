---
source: 029b6094446c707896abd078da818c4746bbebec
---

# 检查、钩子和错误

![Bostie 双臂交叉，气鼓鼓的](../assets/mascot/bostie-angry.webp){ align=right width="180" loading=lazy }

检查决定一个处理函数能否运行。钩子在处理函数之前和之后运行代码。错误钩子在出现异常时作出回应。

## 检查 { #checks }

```python
--8<-- "docs/snippets/checks/command_checks.py"
```

检查是一个接收上下文并返回 `bool`（或 await 后得到 `bool`）的函数。返回 `False` 会以 `CheckFailure` 拒绝这次运行，检查也可以自己抛出一个 `CheckFailure`：它的消息就是告诉用户的内容。检查放在命令、命令组、子命令、用户命令或消息命令、入口点命令，或组件路由和模态框路由的 `checks=[...]` 设置中，`@router.check` 则为某个路由器的每个处理函数注册一个检查；机器人本身也是一个路由器。

机器人的检查最先运行，然后是各个路由器的检查（从最外层向内），然后是命令组的检查（自上而下），最后是命令自己的检查。第一次拒绝就会终止运行。自动补全不运行任何检查。

`bostie.checks` 提供了常用的检查：

| 检查 | 拒绝时引发 | 何时拒绝 |
|---|---|---|
| `has_permissions(permissions)` | `MissingPermissions` | 用户在该频道中缺少 `permissions` 中的任何一项，或者处于私信中 |
| `bot_has_permissions(permissions)` | `BotMissingPermissions` | 机器人在该频道中缺少其中任何一项 |
| `Cooldown(rate, seconds, per="user")` | `CooldownActive` | 该键在最近 `seconds` 秒内已经使用了 `rate` 次 |
| `MaxConcurrency(number, per="global")` | `MaxConcurrencyReached` | 该键仍有 `number` 次运行在进行中 |
| `has_role(role)` | `MissingRole` | 用户没有该身份组（以 ID 或名称给出），或者处于私信中 |
| `has_any_role(*roles)` | `MissingRole` | 用户没有其中任何一个身份组 |
| `is_owner` | `NotOwner` | 用户不在 `bot.owner_ids` 之中 |

如果某个检查对象还有一个 `release(ctx)` 方法，即像 `MaxConcurrency` 这样的 `checks.ReleasingCheck`，那么处理函数结束后，无论以何种方式结束，它都会被释放。

`per` 可以是 `"user"`、`"guild"`（在私信中按用户）、`"channel"`、`"global"`，或一个接收上下文并返回任意可哈希键的函数。权限检查读取的是 Discord 随交互一起发送的权限，因此既不需要缓存，也不需要发请求。它们的错误带有 `missing`，即缺少的 `Permissions`，`CooldownActive` 则带有以秒为单位的 `retry_after`。

## 身份组和所有者 { #roles-and-owners }

```python
--8<-- "docs/snippets/checks/staff_checks.py"
```

`has_role` 和 `has_any_role` 接受身份组 ID 或名称。ID 与 Discord 随交互一起发送的身份组进行匹配，因此不需要缓存。名称则在缓存中该服务器的身份组里查找，所以没有缓存的机器人或[仅使用 HTTP 的应用](http-interactions.md)应该用 ID 指定身份组。`MissingRole` 带有所要求的 `roles`，对于以 ID 给出的身份组，它的消息会提及该身份组，Discord 会将其显示为身份组名称。

`is_owner` 本身就是一个检查，不用加括号。`bot.owner_ids` 保存它放行的用户：传给 `Bot(...)` 的 `owner_ids`，否则为应用的所有者，在机器人启动时从应用信息中读取。对于属于某个团队的应用，它们是团队所有者以及已接受邀请的管理员和开发者；只读成员不包括在内。

## 告诉用户什么 { #what-the-user-is-told }

当没有错误钩子处理 `CheckFailure` 时，bostie 会以仅自己可见的方式回应它的消息：“You need the Manage Messages permission.”、“The bot needs the Manage Messages permission.”、带相对时间戳（例如“in 42 seconds”）的“Try again”、“This is already running; try again once it is done.”、“You need the Moderator role.”、“Only the bot's owners can use this.”，对于单纯返回的 `False` 则是“You cannot use this.”。

## 前置和后置钩子 { #hooks-before-and-after }

```python
--8<-- "docs/snippets/checks/error_hooks.py"
```

`@router.before_invoke` 注册一个 `async def hook(ctx)`，它在检查通过后、处理函数之前运行；`@router.after_invoke` 注册的钩子在处理函数之后运行，处理函数抛出异常时也会运行。它们会为该路由器的每条命令、每个组件路由和模态框路由运行，所以可能遇到组件的钩子不应假定拿到的是 `Context`。机器人的前置钩子最先运行，后置钩子最后运行。后置钩子抛出的异常会被记录，不会改变回应。

## 错误钩子 { #error-hooks }

来自检查、转换器、前置钩子或处理函数的异常都会交给错误钩子。`@x.on_error` 为所有 `Exception` 注册 `async def hook(ctx, error)`，`@x.on_error(NotFound, ValueError)` 则为这些类型及其子类注册。`x` 可以是命令、命令组、组件路由或模态框路由、路由器或机器人，每个对象对每种异常类型可以有一个钩子。

bostie 按以下顺序查找钩子，找到的第一个负责处理该错误：

1. 命令本身，然后是它的命令组和顶层命令组；
2. 命令所属的路由器，然后是包含它的每个路由器，一直到机器人。

在同一个位置上，与错误类最接近的钩子优先：针对 `NotFound` 的钩子优先于针对 `HTTPException` 的钩子。重新抛出同一个错误的钩子会把它按上述顺序传给下一个钩子，就像上面的 `slow_down` 那样。抛出其他错误的钩子，其错误会被记录。

上下文类型是 `ErrorContext`：即 `Context`、`ComponentContext`、`ModalContext` 或 `AutocompleteContext`。自动补全上下文不能调用 `respond`，所以要像上面那样先检查它。

钩子没有回应就返回时，bostie 会记录一条警告，并回应“Something went wrong.”。没有钩子处理该错误时，如果是 `CheckFailure`，bostie 会以它的消息回应；如果是其他错误，则先记录错误及其回溯信息，再回应“Something went wrong.”。如果处理函数已经回应过，则不会再回应任何内容；自动补全则会收到一个空的建议列表。

## 处理函数之外的错误 { #errors-outside-handlers }

监听器和任务没有可以回应的用户。监听器中的异常会作为 `ListenerError` 事件发出，任务中的异常则作为 `TaskError` 事件发出；如果没有监听它们，bostie 会把它们记录到日志中。参见[事件](events.md#when-a-listener-raises)和[后台任务](tasks.md)。

## 异常类型 { #exception-types }

bostie 抛出的与 Discord、网络或机器人状态有关的异常都是 `BostieError`。参数错误则引发 `ValueError` 或 `TypeError`。有些错误同时也是标准异常：`StateError` 是 `RuntimeError`，`MissingIntent` 是 `ValueError`，`StartupError` 是 `OSError`，`CommandDefinitionError` 是 `TypeError`。与命令和交互相关的错误共享基类 `CommandError`，语音错误的基类是 `VoiceError`。

| 异常 | 何时引发 |
|---|---|
| `HTTPException` | REST 请求以错误状态码结束；带有 `status`、`code`（一个 `JSONErrorCode`）、`message`、`errors` 和原始的 `body` |
| `BadRequest`、`Forbidden`、`NotFound`、`ServerError` | 400、403、404 和 5xx |
| `RateLimited` | 429，或者 bostie 拦下了一个需要等待超过 `max_rate_limit_wait` 的请求；带有 `retry_after`、`is_global` |
| `InvalidToken` | Discord 在 REST 或网关上拒绝了令牌 |
| `TransportError` | 请求在 Discord 回应之前就失败了 |
| `UnexpectedResponse` | Discord 接受了请求，但它的回应无法解码；`raw` 保存了该回应 |
| `RequestBudgetExhausted` | 最近的无效请求过多，bostie 为保护 IP 拒绝了一个请求 |
| `GatewayClosed`、`PrivilegedIntentsRequired`、`ShardingRequired` | Discord 永久关闭了网关；后两者对应关闭码 4014 和 4011 |
| `ShardNotRunning`、`GatewayQueueFull` | 发往本进程未运行的分片的网关命令，或排队等待的命令过多 |
| `StateError` | 对象当前状态不允许的调用，例如在 `start()` 之前访问 `bot.rest` |
| `MissingIntent` | 某项功能需要一个机器人没有申请的 Intent |
| `MemberRequestRateLimited` | Discord 拒绝了一次成员请求；带有 `retry_after` |
| `StartupError` | bostie 无法设置它所需的某项系统资源 |
| `CommandDefinitionError` | 命令、命令组或选项违反了 Discord 的规则；由装饰器引发 |
| `CommandSyncError` | Discord 在同步时拒绝了命令 |
| `OptionConversionError` | 选项或目标无法转换为其类型提示 |
| `InteractionResponded`、`InteractionNotAnswered`、`InteractionExpired` | 交互当前状态不允许的回应调用 |
| `CheckFailure` 及其子类 | 某个检查拒绝了运行 |

语音错误见[语音页面](../voice/index.md#errors)。[错误参考](../reference/api/errors.md)列出了每个类及其属性。
