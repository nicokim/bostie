---
source: 0c49168c9e0add898da9cb6bf25e6b0a9fb68ecd
description: "Приложения Discord для установки пользователями, запуск Активностей, продажа SKU через entitlements и подписки и управление лобби Social SDK через REST."
---

# Активности, монетизация и Social SDK

Приложения Discord — это не только боты на серверах. Эта страница описывает приложения, установленные для пользователей, Активности, продажу SKU и серверную сторону Social SDK.

```python
--8<-- "docs/snippets/apps/premium.py"
```

## Приложения, установленные для пользователей { #apps-installed-to-users }

Приложение можно установить не только на сервер, но и в аккаунт пользователя, и тогда его команды работают везде, где находится этот пользователь, включая личные сообщения с друзьями и серверы без бота. Включите установку для пользователя на странице **Installation** в Developer Portal, затем разрешите командам её использовать через `integration_types` и `contexts`, как пример делает для всего бота ([«Регистрация команд»](registration.md#where-commands-can-be-used)).

В таком контексте бота нет в канале: нет записи в кэше, нет событий шлюза, и REST API отклоняет там большинство вызовов. `ctx.interaction.authorizing_integration_owners` сообщает, какие установки сделали взаимодействие возможным, `ctx.interaction.context` — где оно произошло, а `ctx.interaction.app_permissions` — что приложению разрешено. Ответы работают как обычно, и Discord разрешает 5 последующих сообщений для приложения, установленного только для пользователя; bostie предупреждает на шестом.

## Активности { #activities }

Активность — это веб-приложение, которое работает внутри Discord. bostie покрывает сторону бота:

- `@bot.entry_point(name)` регистрирует команду точки входа, которую показывает панель запуска приложений (App Launcher), как объясняет раздел [«Команды пользователя, сообщения и точки входа»](context-menus.md#the-entry-point-command).
- `await ctx.launch_activity()` открывает Активность из команды или компонента и возвращает ID её экземпляра.
- `await bot.rest.application.fetch_activity_instance(application_id, instance_id)` возвращает `ActivityInstance`: где она запущена и ID пользователей в ней.

[Пример Активности](../examples.md#activity) регистрирует точку входа и слэш-команду, которые обе её запускают.

## Монетизация { #monetization }

Приложение с включённой монетизацией продаёт SKU: подписки, постоянные предметы и расходуемые предметы. Покупки пользователя — это entitlement.

- `ctx.interaction.entitlements` перечисляет entitlement пользователя и сервера взаимодействия, так что команда может проверить покупку без запроса.
- `ui.premium_button(sku_id)` показывает кнопку, которая открывает покупку SKU; до бота она никогда не доходит. `fmt.store(application_id, sku_id=None)` даёт ссылку на страницу магазина приложения или на один SKU, которую Discord показывает карточкой.
- `bot.rest.monetization` перечисляет SKU приложения (`list_skus`), перебирает entitlement с фильтрами по пользователю, серверу и SKU (`list_entitlements`), получает один из них и отмечает расходуемый предмет использованным (`consume_entitlement`). Он также перечисляет и получает подписки на SKU.
- `create_test_entitlement(application_id, sku_id=..., owner_id=..., owner_type=1)` выдаёт бесплатный тестовый entitlement серверу (1) или пользователю (2), а `delete_test_entitlement` его удаляет.
- События `EntitlementCreate`, `EntitlementUpdate` и `EntitlementDelete`, а также `SubscriptionCreate`, `SubscriptionUpdate` и `SubscriptionDelete` сообщают об изменениях через шлюз; события entitlement приходят и как [события вебхуков](http-interactions.md#webhook-events). Тестовый entitlement приходит как `PartialEntitlement`, без дат, которые есть у настоящего.

## Social SDK { #social-sdk }

Discord Social SDK работает внутри игр. Его серверная сторона — это REST:

- `bot.rest.lobbies` создаёт лобби, изменяет их метаданные и участников (`add_member`, `bulk_update_members` — до 25 за раз, `remove_member`), удаляет их, приглашает пользователя в канал, связанный с лобби, и задаёт метаданные модерации для сообщений лобби.
- `user.rest.lobbies` на [`BearerClient`](oauth2.md#calling-the-api-as-the-user) с токеном игрока создаёт лобби или входит в него по секрету (`create_or_join`), выходит из него (`leave`), связывает канал Discord и отменяет связь (`link_channel`, `unlink_channel`), отправляет и перечисляет сообщения (`send_message`, `list_messages`) и приглашает самого игрока в связанный канал (`create_invite_for_self`).
- `bot.rest.partner_sdk` обменивает токены: временный токен для игрока из собственной системы аккаунтов игры, токен в обмен на токен внешнего поставщика удостоверений, токен дочерней игры в обмен на токен издателя; он также разъединяет временные аккаунты и задаёт метаданные модерации для личных сообщений.
- `bot.rest.identities` перечисляет, получает и обновляет удостоверения и профили приложения у пользователей, которые связали внешний аккаунт.

Сообщения лобби и личные сообщения в игре приходят как [события вебхуков](http-interactions.md#webhook-events) `LobbyMessage*` и `GameDirectMessage*`.
