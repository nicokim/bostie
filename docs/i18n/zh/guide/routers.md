---
source: 54b3a6f7e7f026cf8621c75603332297ea4c5a00
---

# 路由器与模块

`Router` 和机器人一样，可以容纳命令、监听器、路由、任务、检查、钩子和错误钩子，但它本身并不是机器人。路由器可以把机器人拆分成模块，为一组命令提供共享的设置，还能在机器人运行时被替换。机器人本身就是一个路由器，所以本页的所有内容同样适用于机器人。

```python
--8<-- "docs/snippets/routers/moderation_router.py"
```

路由器拥有机器人的所有装饰器：`command`、`group`、`user_command`、`message_command`、`entry_point`、`listen`、`listen_raw`、`on_component`、`on_modal`、`every`、`check`、`before_invoke`、`after_invoke` 和 `on_error`。`bot.include(router)` 会把这些内容全部加到机器人上，之后在该路由器上注册的内容也会一并加入。在其处理函数中，`router.bot` 就是引入它的那个机器人；在被引入之前访问它会引发 `StateError`。

路由器可以嵌套：`router.include(other)` 的工作方式相同，把外层路由器引入机器人时，两者都会被引入。

## 默认值 { #defaults }

`Router(...)` 以关键字参数的形式，为注册在其上的命令和路由提供默认值：

| 默认值 | 适用于 |
|---|---|
| `ephemeral` | 其命令和路由的回应 |
| `auto_defer` | 其命令和路由 |
| `default_permissions` | 其顶层命令 |
| `nsfw` | 其顶层命令 |
| `contexts`、`integration_types` | 其全局命令 |

命令或路由首先使用自己的设置，然后是其路由器的默认值，接着是每个包含它的路由器的默认值（由近及远），最后是机器人的默认值。在这个例子中，`/ban` 对没有“封禁成员”权限的成员隐藏，只能在服务器中使用，并且以仅自己可见的方式回应，因此只有执行它的版主才能看到并按下按钮。

路由器的检查、前置钩子和后置钩子作用于它的处理函数；包含它的那些路由器的检查和钩子也会运行，最外层的最先运行。错误钩子从最近的路由器开始向外查找，[检查、钩子和错误](checks.md#error-hooks)中有说明。

## 模块 { #modules }

`bot.include("greetings")` 会导入模块 `greetings`，并引入它名为 `router` 的那个 `Router`。没有该对象的模块会引发 `TypeError`。

`bot.exclude("greetings")` 会移除该路由器添加的所有内容，取消它的任务，并让 Python 忘掉该模块及其子模块，因此再次引入时会重新导入文件。`bot.reload("greetings")` 一次完成这两步：如果新的导入或引入失败，旧的路由器会保持引入状态，并抛出该错误。重新加载会立即替换处理函数；如果命令定义发生了变化，之后请调用 `await bot.sync_commands()`，就像[模块化示例](../examples.md#modular-bot)在它的 `/reload` 命令中做的那样。

`exclude` 和 `reload` 也接受 `Router` 对象，用于那些不是某个模块的 `router` 的路由器。

## 冲突 { #conflicts }

两条同名命令、两条同名的用户命令或消息命令、两个模板相同的路由，或者同一个函数被注册为两次任务，都会在添加第二个时抛出异常，由路由器带入时也是如此。一个路由器只能被引入一次，并且永远不能引入它自己。

## 查看内部 { #looking-inside }

`router.commands`、`router.context_menus`、`router.component_routes`、`router.modal_routes`、`router.tasks` 和 `router.listeners` 保存着已注册的内容：命令按名称和服务器索引，例如全局命令为 `("rules", ())`；用户命令和消息命令按类型、名称和服务器索引；路由按模板索引；任务按函数索引；监听器按事件索引。`router.walk_commands()` 会依次产出每条有处理函数的斜杠命令和子命令。`router.match_component(custom_id)` 和 `router.match_modal(custom_id)` 返回某个自定义 ID 会到达的路由及其占位符的值，或者 `None`，`router.find_error_handlers(error)` 则按由近及远的顺序产出会处理某个异常的错误钩子。
