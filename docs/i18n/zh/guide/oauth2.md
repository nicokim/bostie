---
source: a95f1211f80aa1fe37d568699ddca680b3c3ed9d
description: "在 bostie 中使用 Discord OAuth2：带 PKCE 的授权 URL、授权码交换与刷新、用用户的令牌调用 API，以及关联身份组元数据。"
---

# OAuth2 与关联身份组

![Bostie 高举一把闪闪发光的钥匙](../assets/mascot/bostie-oauth2.webp){ align=right width="180" loading=lazy }

OAuth2 让用户可以授权你的应用访问其账号：个人资料、所在的服务器、已关联的账号、把用户加入某个服务器的权利，或者用于关联身份组的身份组连接（role connection）。bostie 负责构建授权 URL，并用用户授予的令牌调用 API。

```python
--8<-- "docs/snippets/oauth2/oauth_flow.py"
```

## 授权 URL { #authorization-urls }

`oauth2.authorize_url(client_id, scopes=[...], redirect_uri=..., state=...)` 返回一个 URL，它请求用户授予 `scopes`，并带着一个 `code` 重定向回 `redirect_uri`。授权范围是 `OAuth2Scope` 的成员，例如对应 `identify` 的 `OAuth2Scope.IDENTIFY`，Discord 文档中的每个授权范围都有对应成员；对于 Discord 新增、bostie 尚未命名的授权范围，可以直接使用字符串。请检查重定向中携带的 `state` 是否与你发送的一致，这样其他网站就无法让用户用它的授权码登录。它还接受：

- `code_challenge`，即 PKCE 校验码（verifier）的 S256 挑战值，`exchange_code` 随后会把校验码作为 `code_verifier` 发送；示例代码中计算了一个；
- `prompt="none"`，对已经授予这些授权范围的用户跳过同意界面，或 `"consent"`，再次显示同意界面；
- `integration_type`，把应用安装到服务器或用户。

bostie 只构建授权码授权（authorization code grant）。Discord 不建议使用隐式授权（implicit grant），因为它会把令牌放在重定向 URL 中，而且不提供刷新令牌，Discord 推荐改用带 PKCE 的授权码授权。

`oauth2.invite_url(client_id, permissions=..., scopes=[...])` 返回应用的安装链接，还接受 `integration_type`、用于预选服务器的 `guild_id` 以及 `disable_guild_select`。它省略的内容都来自 Developer Portal 中应用的安装设置。

## 令牌 { #tokens }

不带令牌的 `BearerClient()` 只能访问 `rest.oauth2`：

| 方法 | 作用 |
|---|---|
| `exchange_code(client_id=, client_secret=, code=, redirect_uri=, code_verifier=None)` | 把重定向中的授权码换成一个 `AccessToken` |
| `refresh(client_id=, client_secret=, refresh_token=)` | 在旧令牌过期之前获取一个新令牌 |
| `revoke(client_id=, client_secret=, token=)` | 终止某个令牌及其签发的所有令牌的授权 |
| `exchange_client_credentials(client_id=, client_secret=, scopes=[...])` | 获取应用所有者（或其团队）的令牌 |

`AccessToken` 带有 `access_token`、`token_type`、以秒为单位的 `expires_in`、`scope`（已授予的授权范围，以空格分隔）和 `refresh_token`。包含 `bot` 授权范围的授权码授权还会返回机器人加入的 `guild`，包含 `webhook.incoming` 的则返回它创建的 `webhook`。令牌的 `repr` 会隐藏机密信息，因此把它写进日志也不会泄露。

## 以用户身份调用 API { #calling-the-api-as-the-user }

`async with BearerClient(access_token) as user:` 可以访问令牌的授权范围所允许的内容：

| `user.rest.` | 方法 | 授权范围 |
|---|---|---|
| `users` | `fetch_current` | `identify` |
| `users` | `list_connections` | `connections` |
| `users` | `fetch_role_connection`、`update_role_connection`、`delete_role_connection` | `role_connections.write` |
| `guilds` | `list` | `guilds` |
| `members` | `fetch_current(guild_id)` | `guilds.members.read` |
| `commands` | `fetch_permissions`、`list_permissions`、`edit_permissions` | 编辑时需要 `applications.commands.permissions.update` |
| `oauth2` | `fetch_authorization`，说明该令牌授予了什么、授予给谁 | 任意 |
| `lobbies` | [Social SDK 大厅](platform.md#social-sdk)中用户一侧的操作 | `sdk.social_layer` |

有了 `guilds.join` 授权范围，机器人可以把用户加入它所在的某个服务器：`await bot.rest.members.add(guild_id, user_id, access_token=token.access_token)`，它返回新的 `Member`，如果用户已经是成员则返回 `None`。

## 关联身份组 { #linked-roles }

关联身份组是一种服务器身份组，用户在与你的应用之间的连接满足某些条件（例如“等级至少为 10”）时就能获得它。应用先声明一次它要上报的值，然后用每个用户的令牌上报其值：

1. 用 `bot.rest.application.update_role_connection_metadata(application_id, [...])` 注册最多 5 条元数据记录，每条都是一个带有 `type`、`key`、`name` 和 `description` 的 `ApplicationRoleConnectionMetadata`。示例代码中的 `register_metadata` 用 `RESTClient` 完成了这一步。
2. 在 Developer Portal 中，把应用的 **Linked Roles Verification URL** 设置为一个以 `role_connections.write` 授权范围发起 OAuth2 流程的页面。
3. 完成授权码交换后，调用 `user.rest.users.update_role_connection(application_id, platform_name=..., platform_username=..., metadata={"level": "12"})`。元数据的值是字符串，这是 Discord API 接受的形式。

[关联身份组示例](../examples.md#linked-roles)是一个完整的验证页面。`fmt.guild_navigation("linked-roles", role_id)` 可以在聊天中链接到服务器的某个关联身份组。
