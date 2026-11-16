---
source: d24ff7127c79eec4345b1d42ca0265273465a567
description: "用 ASGI 应用代替网关，通过 HTTP 处理 Discord 交互和 Webhook 事件，验证请求签名，或使用你自己的 Web 服务器。"
---

# HTTP 交互与 Webhook 事件

Discord 可以把交互发送到你应用的某个 URL，而不是通过网关发送，还有一些事件，即 Webhook 事件，只会发送到 URL。`ASGIApp` 用同一个机器人同时处理这两者，机器人的命令、路由和监听器都无需改动。

```python
--8<-- "docs/snippets/http/http_bot.py"
```

`bostie.ASGIApp(bot)` 是一个 ASGI 应用：用任意 ASGI 服务器在 HTTPS 之后提供 `app` 服务即可。然后在 Developer Portal 中，把应用的 **Interactions Endpoint URL**，以及（用于 Webhook 事件的）**Webhook Events URL** 和你需要的事件类型，设置为它监听的地址。保存端点时，Discord 会发送一个 PING 和一个签名错误的请求来检查它，`ASGIApp` 会按 Discord 的预期回应这两者。

## 它做了什么 { #what-it-does }

服务器通过 ASGI lifespan 启动时，除非机器人已经在网关上运行，否则应用会调用 `bot.open()`，它会：

1. 打开机器人的 HTTP 客户端；
2. 获取应用信息（用于取得应用 ID 和为 Discord 请求签名的公钥）以及机器人的用户；
3. 发出 `Starting`，同步命令，并启动[后台任务](tasks.md)。

这一步失败时，服务器的启动会因该错误而中止。服务器关闭时，如果机器人是由应用启动的，应用会关闭它。

每个请求都会先经过检查：只接受 `POST`（否则返回 405），请求体上限为 4 MiB（413），`X-Signature-Ed25519` 无法用公钥验证的请求返回 401。然后：

- 交互 PING 得到 Discord 期望的 PONG。
- 其他任何交互都以 202 回应，并交给机器人，机器人会像处理网关交互一样运行相应的命令、路由或模态框处理函数；处理函数通过 Discord 的 REST API 回应，即使用 `ctx.respond` 等方法。[自动延迟回应](responses.md#deferring)同样有效。
- Webhook 事件会到达机器人的监听器并得到 204，Discord 发往 Webhook Events URL 的 PING 也是如此。

`ASGIApp(bot, token=..., public_key=...)` 接受令牌（否则使用 `BOSTIE_TOKEN`）和公钥（否则使用应用信息中报告的公钥）。

## 使用或不使用网关 { #with-or-without-the-gateway }

单独提供服务时，应用没有网关连接：没有网关事件，没有缓存更新，也没有语音。命令、组件、模态框、REST 和 Webhook 事件都可以正常工作。如果两者都需要，就在同一个事件循环中同时在网关上启动机器人，应用会使用这个正在运行的机器人；此时交互通过 HTTP 到达，而网关仍然会投递事件。

## Webhook 事件 { #webhook-events }

| 事件 | 何时发出 |
|---|---|
| `ApplicationAuthorized` | 用户把应用添加到某个服务器或自己的账号 |
| `ApplicationDeauthorized` | 用户移除了应用，或者应用的 OAuth2 令牌已失效 |
| `EntitlementCreate`、`EntitlementUpdate`、`EntitlementDelete` | 某个权益发生了变化 |
| `QuestUserEnrollment` | 用户被加入某个任务；Discord 已有文档说明，但目前还不会把它发送给应用 |
| `LobbyMessageCreate`、`LobbyMessageUpdate`、`LobbyMessageDelete` | Social SDK 大厅中有消息被发送、编辑或删除 |
| `GameDirectMessageCreate`、`GameDirectMessageUpdate`、`GameDirectMessageDelete` | Social SDK 会话中的某条私信发生了变化 |

像监听网关事件一样，用 `@bot.listen` 监听它们。bostie 尚未建模的 Webhook 事件类型会以 `RawEvent` 的形式到达。

## 在你自己的 Web 服务器中 { #in-your-own-web-server }

`ASGIApp` 用到的两个组件都是公开的，可以用在你已经在运行的 Web 框架中：

```python
--8<-- "docs/snippets/http/own_server.py"
```

`verify_signature(public_key, signature, timestamp, body)` 用应用的十六进制公钥检查请求的 `X-Signature-Ed25519` 和 `X-Signature-Timestamp` 请求头；它返回 `False` 时请回应 401。`decode_webhook_event(body)` 返回 Webhook Events 请求对应的带类型事件，对于 bostie 尚未建模的类型返回 `RawEvent`，对于 PING 返回 `None`。至于在你自己的服务器中处理交互，[HTTP 交互示例](../examples.md#http-interactions)展示了 ASGI 的方式，这也是 bostie 所支持的方式。
