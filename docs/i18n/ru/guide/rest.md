---
source: 013ae18bd66c3357e9ff74c65156f6ac85917bf1
description: "Все эндпоинты REST API Discord для ботов: постраничный перебор, массовое удаление сообщений, журнал аудита, лимиты запросов, повторы и скачивание файлов."
---

# REST API

![Бости прыгает за мячом](../assets/mascot/bostie-jumping.webp){ align=right width="180" loading=lazy }

В `bot.rest` есть метод для каждой операции REST API Discord, доступной боту, и методы сгруппированы по ресурсу, с которым работают: `bot.rest.messages.create`, `bot.rest.members.add_role`, `bot.rest.guilds.fetch`. Запросы идут через HTTP-клиент bostie на Rust, который соблюдает лимиты запросов Discord.

```python
--8<-- "docs/snippets/rest/rest_calls.py"
```

## Соглашения { #conventions }

- Сначала идут ID из URL — только позиционно и в порядке URL: `bot.rest.members.add_role(guild_id, user_id, role_id)`.
- Всё остальное — именованные аргументы с именами из документации Discord, проверяемые по `TypedDict` для каждой операции, так что опечатка — это ошибка типов и `TypeError` во время выполнения.
- Операции, которые Discord записывает в журнал аудита, принимают `reason=` длиной от 1 до 512 символов — его видят модераторы сервера.
- Поле изображения или звука, например `image` эмодзи или `icon` сервера, принимает `File`; bostie отправляет его как data URI, которого ждёт Discord. Поля прав принимают `Permissions`.
- Эмодзи, например тот, который добавляет `reactions.create`, принимает эмодзи Unicode, собственный эмодзи в виде `<:name:id>`, `<a:name:id>` или `name:id` или модель `Emoji` либо `PartialEmoji`.
- Методы с именем `fetch` получают один объект, `list` перебирает несколько, `edit` изменяет, `create` создаёт, а `delete` удаляет. Остальные глаголы сохраняют слово из документации, например `members.add_role` или `guilds.begin_prune`.
- Методы возвращают модели или `None`, если Discord отвечает без содержимого. Они не меняют `bot.cache`, за исключением того, что `channels.create_dm` сохраняет личный канал, когда это позволяет `max_dm_channels`; кэш обновляют события шлюза, которые следуют за изменением.

## Постраничный перебор { #paging }

Методы `list` для эндпоинтов, которые Discord отдаёт по страницам, возвращают асинхронный итератор, который получает следующую страницу по ходу перебора: `async for message in bot.rest.messages.list(channel_id, limit=500):`, как в `/today` выше. Без `limit` он проходит все элементы. `before`, `after` и `around` начинают с другого места, причём можно указать не больше одного из них; они принимают ID или `datetime`, который bostie превращает в ID этого момента. Сообщения идут от новых к старым, а с `after` — от старых к новым. Метод `list` эндпоинта, который Discord не разбивает на страницы, — это корутина, возвращающая кортеж. `audit_log.list` выдаёт целые объекты `AuditLogPage` с пользователями, вебхуками и другими объектами, на которые ссылаются записи.

## Массовое удаление сообщений { #purging-messages }

`messages.purge(channel_id, limit=100, check=None)` проходит последние `limit` сообщений от новых к старым и удаляет те, для которых `check` возвращает истину, или все, если `check` не задан; `limit=None` проходит весь канал, а `before` и `after` начинают с другого места, как в `list`. Сообщения моложе 14 дней удаляются пакетами до 100 штук, более старые — по одному запросу на каждое, поскольку Discord пакетно удаляет только молодые сообщения; тип, который Discord не позволяет удалять, например стартовое сообщение ветки, пропускается. `bulk=False` удаляет по одному, как в личных сообщениях, где пакетного удаления нет. Метод возвращает удалённые сообщения, а `reason` попадает в журнал аудита. Для пакетного удаления и удаления чужих сообщений нужно право «Управлять сообщениями».

```python
--8<-- "docs/snippets/rest/purge.py"
```

## Журнал аудита { #the-audit-log }

`audit_log.list(guild_id)` выдаёт объекты `AuditLogPage`, а шлюз присылает каждую новую запись как событие `AuditLogEntryCreate`. `changes` записи содержат по одному `AuditLogChange` на каждое изменённое поле, у которого `new_value` и `old_value` имеют тот тип, который это поле имеет у изменённого объекта: `Permissions` для `permissions` роли, `datetime` для `communication_disabled_until` участника, кортеж `Overwrite` для `permission_overwrites` канала. Исключения Discord следуют его документации: `$add` и `$remove` для ролей участника хранят объекты `AuditLogRole`, `avatar_hash` вебхука и `channel_id` приглашения получают тип `avatar` и ID соответственно, а изменение прав команды индексируется ID роли, пользователя или канала и хранит `CommandPermission`. Ключ, которого bostie не знает, или значение в форме, которую поле не принимает, сохраняет своё значение JSON, а значение, которое Discord не прислал, равно `None`.

```python
--8<-- "docs/snippets/rest/audit_log.py"
```

## Ошибки { #errors }

Запрос, на который Discord отвечает статусом ошибки, вызывает `HTTPException`: `BadRequest` для 400, `Forbidden` для 403, `NotFound` для 404, `ServerError` для 5xx. У исключения есть `status`, `code` Discord в виде `JSONErrorCode`, его `message`, ошибки по полям `errors` и сырое `body`; в тексте исключения каждая ошибка поля стоит на своей строке. `UnexpectedResponse` означает, что Discord принял запрос, но его ответ не декодировался; в сообщении сказано, вступило ли изменение в силу, а `error.raw` хранит ответ.

## Лимиты запросов, повторы и тайм-ауты { #rate-limits-retries-and-timeouts }

Клиент на Rust отслеживает бакет лимита запросов каждого маршрута и глобальный лимит Discord и придерживает запрос, пока в его бакете не появится место. `Bot(http_settings=HTTPSettings(...))` меняет поведение:

| Поле `HTTPSettings` | По умолчанию | Значение |
|---|---|---|
| `global_rate_limit` | 50 | запросов в секунду по всем маршрутам; увеличьте, если Discord дал боту более высокий лимит |
| `max_rate_limit_wait` | 30.0 | максимальное ожидание из-за лимита запросов; запрос, который ждал бы дольше, вызывает `RateLimited` и не отправляется |
| `retries` | 2 | сколько раз повторяется неудавшийся запрос, с экспоненциальной задержкой не больше `max_rate_limit_wait` |
| `connect_timeout` | 10.0 | секунд на открытие соединения |
| `read_timeout` | 60.0 | секунд на ожидание ответа |
| `rate_limiter` | `True` | `False` отправляет сразу и лишь выжидает вернувшиеся 429 — для работы за прокси лимитов запросов |
| `identify_spacing` | 5.0 | секунд между двумя identify на шлюзе и перед повторным identify после кода закрытия 4007; Discord требует 5, поэтому уменьшайте значение только при работе с `bostie.testing` |
| `proxy` | `None` | URL прокси HTTP или HTTPS для всех запросов, включая звук `voice.Url` и загрузки из CDN, с учётными данными в виде `http://user:password@host:3128` |

429 повторяется после задержки, которую указывает Discord, если она укладывается в `max_rate_limit_wait`. 500, 502, 503, 504 или 524, а также оборвавшееся соединение повторяются `retries` раз для всех методов, кроме `POST`, который мог уже сработать; соединение, которое так и не открылось, повторяется для любого метода.

Discord банит IP, который отправил 10 000 недействительных запросов (401, 403, 429) за 10 минут. bostie их считает и пишет предупреждение в лог на 1000, 5000 и 9000. На 9500 он отклоняет каждый запрос с `RequestBudgetExhausted` вместо отправки, пока счётчик за последние 11 минут не опустится ниже 9000.

## Любой эндпоинт { #any-endpoint }

`bot.http.request(method, route, **params)` отправляет любой запрос через тот же клиент и возвращает декодированный JSON или `None` для пустого ответа. Маршрут сохраняет свои плейсхолдеры, которые заполняют именованные аргументы: `await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=123)`. Метод принимает `json=`, `files=` для multipart-загрузки, `form=` для именованных полей формы, `urlencoded=` для тела form-urlencoded, `query=` и `reason=` и выбрасывает те же исключения. `bot.http.send(...)` принимает те же аргументы и возвращает сырые байты ответа — для эндпоинта, который отвечает не JSON.

## Скачивание файлов { #downloading-files }

Вложения и изображения лежат в CDN Discord, а не за API, поэтому при скачивании токен не отправляется и лимит запросов не расходуется. `bot.http` получает их через тот же клиент на Rust, с его тайм-аутами, повторами и `proxy`:

| Метод | Что делает |
|---|---|
| `read(source)` | возвращает байты |
| `save(source, path)` | записывает их в файл по пути `path` |
| `to_file(source)` | возвращает `File` для повторной загрузки; из `Attachment` он сохраняет имя файла, описание, заголовок, спойлер и данные голосового сообщения, а из URL берёт имя из последней части пути |

`source` — это `Attachment` или URL, например построенный функциями [`bostie.cdn`](formatting.md). Отсутствующий файл вызывает `NotFound`.

```python
--8<-- "docs/snippets/rest/downloads.py"
```

## Без бота { #without-a-bot }

Скрипт или веб-бэкенд, которому нужен только REST, использует `RESTClient`:

```python
--8<-- "examples/rest_script.py"
```

`async with RESTClient(token) as client:` открывает HTTP-клиент; в `client.rest` есть все группы `bot.rest`, а `client.http` — это сырой клиент. Токен по умолчанию берётся из `BOSTIE_TOKEN`, а клиент принимает `api_url`, `http_settings` и `allowed_mentions`, как и бот. [Руководство по OAuth2](oauth2.md) описывает `BearerClient`, который вызывает API с токеном пользователя, а [руководство по вебхукам](webhooks.md) — клиенты, которым токен вообще не нужен.

## Группы { #the-groups }

| `bot.rest.` | Методы |
|---|---|
| `application` | `edit`, `fetch`, `fetch_activity_instance`, `fetch_bot_application`, `list_role_connection_metadata`, `update_role_connection_metadata` |
| `audit_log` | `list` |
| `auto_moderation` | `create`, `delete`, `edit`, `fetch`, `list` |
| `bans` | `bulk_create`, `create`, `fetch`, `list`, `remove` |
| `channels` | `add_recipient`, `create`, `create_dm`, `create_group_dm`, `delete`, `delete_permission`, `edit`, `edit_permissions`, `edit_positions`, `fetch`, `fetch_member_permissions`, `fetch_role_permissions`, `follow`, `list`, `remove_recipient`, `set_voice_status`, `trigger_typing_indicator` |
| `commands` | `bulk_overwrite`, `bulk_overwrite_guild`, `create`, `create_guild`, `delete`, `delete_guild`, `edit`, `edit_guild`, `fetch`, `fetch_guild`, `fetch_permissions`, `list`, `list_guild`, `list_permissions` |
| `emojis` | `create`, `create_application`, `delete`, `delete_application`, `edit`, `edit_application`, `fetch`, `fetch_application`, `list`, `list_application` |
| `gateway` | `fetch`, `fetch_bot` |
| `guilds` | `begin_prune`, `edit`, `edit_incident_actions`, `edit_welcome_screen`, `edit_widget`, `fetch`, `fetch_preview`, `fetch_prune_count`, `fetch_vanity_url`, `fetch_welcome_screen`, `fetch_widget`, `fetch_widget_image`, `fetch_widget_settings`, `leave`, `list` |
| `identities` | `delete`, `fetch_profile`, `list`, `list_by_external_id`, `update_profile` |
| `integrations` | `delete`, `list` |
| `invites` | `add_target_user`, `bulk_add_target_users`, `bulk_delete_target_users`, `create`, `delete`, `fetch`, `fetch_target_users_job_status`, `list`, `list_guild`, `list_target_users`, `remove_target_user`, `update_target_users` |
| `lobbies` | `add_member`, `bulk_update_members`, `create`, `create_invite_for_user`, `delete`, `edit`, `fetch`, `remove_member`, `update_message_moderation_metadata` |
| `members` | `add`, `add_role`, `edit`, `edit_current`, `fetch`, `fetch_permissions`, `list`, `remove`, `remove_role`, `search` |
| `messages` | `bulk_delete`, `create`, `crosspost`, `delete`, `edit`, `fetch`, `list`, `list_pins`, `pin`, `purge`, `search`, `unpin` |
| `monetization` | `consume_entitlement`, `create_test_entitlement`, `delete_test_entitlement`, `fetch_entitlement`, `fetch_subscription`, `list_entitlements`, `list_skus`, `list_subscriptions` |
| `onboarding` | `edit`, `fetch` |
| `partner_sdk` | `create_provisional_token`, `exchange_child_token`, `exchange_external_token`, `unmerge_external_account`, `unmerge_provisional_account`, `update_dm_moderation_metadata` |
| `polls` | `end`, `list_answer_voters` |
| `reactions` | `create`, `delete`, `delete_all`, `delete_all_for_emoji`, `delete_own`, `list` |
| `roles` | `create`, `delete`, `edit`, `edit_positions`, `fetch`, `fetch_member_counts`, `fetch_permissions`, `list` |
| `scheduled_events` | `create`, `delete`, `edit`, `fetch`, `list`, `list_users` |
| `soundboard` | `create`, `delete`, `edit`, `fetch`, `list`, `list_default`, `send` |
| `stage_instances` | `create`, `delete`, `edit`, `fetch` |
| `stickers` | `create`, `delete`, `edit`, `fetch`, `fetch_guild`, `fetch_pack`, `list`, `list_packs` |
| `templates` | `create`, `delete`, `edit`, `fetch`, `list`, `sync` |
| `threads` | `add_member`, `fetch_member`, `join`, `leave`, `list_active`, `list_joined_private_archived`, `list_members`, `list_private_archived`, `list_public_archived`, `remove_member`, `start`, `start_from_message`, `start_in_forum` |
| `users` | `edit_current`, `fetch`, `fetch_current` |
| `voice` | `edit_current_state`, `edit_state`, `fetch_current_state`, `fetch_state`, `list_guild_regions`, `list_regions` |
| `webhooks` | `create`, `delete`, `delete_message`, `delete_with_token`, `edit`, `edit_message`, `edit_with_token`, `execute`, `execute_github`, `execute_slack`, `fetch`, `fetch_message`, `fetch_with_token`, `list`, `list_guild` |

Параметры и возвращаемый тип каждого метода приведены в [справочнике по REST API](../reference/api/rest.md). Несколько операций намеренно оставлены для `bot.http.request`: эндпоинты, которые Discord объявил устаревшими или так и не задокументировал, а также эндпоинты обратного вызова взаимодействия и исходного ответа, которые покрывают [контексты взаимодействий](responses.md).

Некоторые группы обслуживают возможности Discord за пределами ботов на серверах: `monetization` и `application` — для [монетизации и Активностей](platform.md), `lobbies`, `identities` и `partner_sdk` — для [Social SDK](platform.md#social-sdk), а `application.update_role_connection_metadata` — для [связанных ролей](oauth2.md#linked-roles).
