---
source: 0c49168c9e0add898da9cb6bf25e6b0a9fb68ecd
description: "构建可由用户安装的 Discord 应用，启动 Activity，通过权益和订阅销售 SKU，并通过 REST 管理 Social SDK 大厅。"
---

# Activity、变现和 Social SDK

Discord 应用不只是服务器里的机器人。本页介绍安装到用户的应用、Activity、出售 SKU，以及 Social SDK 的服务端。

```python
--8<-- "docs/snippets/apps/premium.py"
```

## 安装到用户的应用 { #apps-installed-to-users }

应用除了可以安装到服务器，也可以安装到用户的账号，这样它的命令就能在该用户所在的任何地方使用，包括与好友的私信以及没有这个机器人的服务器。在 Developer Portal 的 **Installation** 页面开启用户安装，然后用 `integration_types` 和 `contexts` 让命令可以使用它，就像示例代码对整个机器人所做的那样（[注册命令](registration.md#where-commands-can-be-used)）。

在这样的场景中，机器人并不在频道里：没有缓存条目，没有网关事件，REST API 在那里也会拒绝大多数调用。`ctx.interaction.authorizing_integration_owners` 说明是哪些安装让这次交互得以发生，`ctx.interaction.context` 说明交互发生在哪里，`ctx.interaction.app_permissions` 说明应用可以做什么。回应照常可用，对于只安装到用户的应用，Discord 允许 5 条后续消息；bostie 会在第 6 条时发出警告。

## Activity { #activities }

Activity 是在 Discord 内部运行的 Web 应用。bostie 负责机器人一侧：

- `@bot.entry_point(name)` 注册显示在应用启动器中的入口点命令，[用户命令、消息命令和入口点命令](context-menus.md#the-entry-point-command)中有说明。
- `await ctx.launch_activity()` 从命令或组件中打开 Activity，并返回其实例 ID。
- `await bot.rest.application.fetch_activity_instance(application_id, instance_id)` 返回一个 `ActivityInstance`：它在哪里运行，以及其中用户的 ID。

[Activity 示例](../examples.md#activity)注册了一个入口点命令和一个斜杠命令，二者都能启动 Activity。

## 变现 { #monetization }

启用了变现功能的应用可以出售 SKU：订阅、永久性商品和消耗品。用户的购买记录就是权益。

- `ctx.interaction.entitlements` 列出交互中用户和服务器的权益，因此命令无需发请求就能检查是否已购买。
- `ui.premium_button(sku_id)` 显示一个用于购买某个 SKU 的按钮；它永远不会触达机器人。`fmt.store(application_id, sku_id=None)` 链接到应用的商店页面或某个 SKU，Discord 会把它显示为一张卡片。
- `bot.rest.monetization` 列出应用的 SKU（`list_skus`），按用户、服务器和 SKU 筛选遍历权益（`list_entitlements`），获取单个权益，以及把消耗品标记为已使用（`consume_entitlement`）。它还可以列出和获取某个 SKU 的订阅。
- `create_test_entitlement(application_id, sku_id=..., owner_id=..., owner_type=1)` 向服务器（1）或用户（2）授予一个免费的测试权益，`delete_test_entitlement` 则将其移除。
- `EntitlementCreate`、`EntitlementUpdate` 和 `EntitlementDelete` 事件，以及 `SubscriptionCreate`、`SubscriptionUpdate` 和 `SubscriptionDelete`，通过网关报告变化；权益事件也会以 [Webhook 事件](http-interactions.md#webhook-events)的形式到达。测试权益以 `PartialEntitlement` 的形式到达，不带真实权益所具有的日期。

## Social SDK { #social-sdk }

Discord Social SDK 运行在游戏中。它的服务端是 REST：

- `bot.rest.lobbies` 用于创建大厅、编辑其元数据和成员（`add_member`；`bulk_update_members`，一次最多 25 个；`remove_member`）、删除大厅、邀请用户进入大厅关联的频道，以及为大厅消息设置审核元数据。
- `user.rest.lobbies` 在持有玩家令牌的 [`BearerClient`](oauth2.md#calling-the-api-as-the-user) 上使用，用于通过密钥创建或加入大厅（`create_or_join`）、离开大厅（`leave`）、关联和取消关联 Discord 频道（`link_channel`、`unlink_channel`）、发送和列出消息（`send_message`、`list_messages`），以及邀请玩家本人进入关联的频道（`create_invite_for_self`）。
- `bot.rest.partner_sdk` 用于交换令牌：为游戏自有账号系统中的玩家获取临时令牌，用外部身份提供方的令牌换取令牌，用发行商的令牌换取子游戏的令牌；它还可以取消合并临时账号，以及为私信设置审核元数据。
- `bot.rest.identities` 用于列出、获取和更新关联了外部账号的用户的应用身份和资料。

大厅消息和游戏内私信以 `LobbyMessage*` 和 `GameDirectMessage*` [Webhook 事件](http-interactions.md#webhook-events)的形式到达。
