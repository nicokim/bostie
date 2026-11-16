---
source: a95f1211f80aa1fe37d568699ddca680b3c3ed9d
description: "OAuth2 Discord с bostie: URL авторизации с PKCE, обмен кода и обновление токена, вызовы API с токеном пользователя и метаданные связанных ролей."
---

# OAuth2 и связанные роли

![Бости поднимает сияющий ключ](../assets/mascot/bostie-oauth2.webp){ align=right width="180" loading=lazy }

OAuth2 позволяет пользователю дать вашему приложению доступ к своему аккаунту: к профилю, серверам, связанным аккаунтам, праву добавить его на сервер или к связи роли для связанных ролей. bostie строит URL авторизации и вызывает API с токеном, который выдал пользователь.

```python
--8<-- "docs/snippets/oauth2/oauth_flow.py"
```

## URL авторизации { #authorization-urls }

`oauth2.authorize_url(client_id, scopes=[...], redirect_uri=..., state=...)` возвращает URL, который просит пользователя предоставить области доступа `scopes` и перенаправляет обратно на `redirect_uri` с `code`. Области доступа — это члены `OAuth2Scope`, например `OAuth2Scope.IDENTIFY` для `identify`, по одному на каждую область доступа из документации Discord; для области доступа, которую Discord добавил раньше, чем bostie дал ей имя, подходит обычная строка. Проверяйте, что перенаправление несёт отправленный вами `state`, чтобы другой сайт не мог заставить пользователя войти со своим кодом. Функция также принимает:

- `code_challenge` — S256-вызов верификатора PKCE, который `exchange_code` затем отправляет как `code_verifier`; пример его вычисляет;
- `prompt="none"`, чтобы пропустить экран согласия для пользователя, который уже предоставил эти области доступа, или `"consent"`, чтобы снова его показать;
- `integration_type`, чтобы установить приложение на сервер или для пользователя.

bostie строит только поток с кодом авторизации. Discord не рекомендует неявный поток (implicit grant), который кладёт токен в URL перенаправления и не даёт токена обновления, и советует вместо него поток с кодом и PKCE.

`oauth2.invite_url(client_id, permissions=..., scopes=[...])` возвращает ссылку установки приложения с `integration_type`, `guild_id` для предварительного выбора сервера и `disable_guild_select`. Всё, что она не указывает, берётся из настроек установки приложения в Developer Portal.

## Токены { #tokens }

`BearerClient()` без токена даёт доступ только к `rest.oauth2`:

| Метод | Что делает |
|---|---|
| `exchange_code(client_id=, client_secret=, code=, redirect_uri=, code_verifier=None)` | превращает код из перенаправления в `AccessToken` |
| `refresh(client_id=, client_secret=, refresh_token=)` | новый токен до истечения старого |
| `revoke(client_id=, client_secret=, token=)` | прекращает авторизацию токена и всех выданных им токенов |
| `exchange_client_credentials(client_id=, client_secret=, scopes=[...])` | токен владельца приложения или его команды |

У `AccessToken` есть `access_token`, `token_type`, `expires_in` в секундах, `scope` (предоставленные области доступа через пробел) и `refresh_token`. Поток с кодом, включавший область доступа `bot`, возвращает ещё и `guild`, на который зашёл бот, а поток с `webhook.incoming` — созданный им `webhook`. `repr` токена скрывает секреты, так что запись токена в лог их не раскрывает.

## Вызов API от имени пользователя { #calling-the-api-as-the-user }

`async with BearerClient(access_token) as user:` даёт доступ к тому, что разрешают области доступа токена:

| `user.rest.` | Методы | Область доступа |
|---|---|---|
| `users` | `fetch_current` | `identify` |
| `users` | `list_connections` | `connections` |
| `users` | `fetch_role_connection`, `update_role_connection`, `delete_role_connection` | `role_connections.write` |
| `guilds` | `list` | `guilds` |
| `members` | `fetch_current(guild_id)` | `guilds.members.read` |
| `commands` | `fetch_permissions`, `list_permissions`, `edit_permissions` | `applications.commands.permissions.update` для изменения |
| `oauth2` | `fetch_authorization`, который сообщает, что разрешает токен и кому | любая |
| `lobbies` | пользовательская сторона [лобби Social SDK](platform.md#social-sdk) | `sdk.social_layer` |

С областью доступа `guilds.join` бот может добавить пользователя на сервер, где состоит сам: `await bot.rest.members.add(guild_id, user_id, access_token=token.access_token)` возвращает нового `Member` или `None`, если пользователь уже был участником.

## Связанные роли { #linked-roles }

Связанная роль — это роль сервера, которую пользователи получают, выполнив условия на связи с вашим приложением, например «уровень не ниже 10». Приложение один раз объявляет значения, которые сообщает, а затем сообщает значения каждого пользователя с его токеном:

1. Зарегистрируйте до 5 записей метаданных через `bot.rest.application.update_role_connection_metadata(application_id, [...])`, каждая — `ApplicationRoleConnectionMetadata` с `type`, `key`, `name` и `description`. В примере это делает `register_metadata` с `RESTClient`.
2. Укажите в Developer Portal в поле **Linked Roles Verification URL** приложения страницу, которая начинает поток OAuth2 с областью доступа `role_connections.write`.
3. После обмена кода вызовите `user.rest.users.update_role_connection(application_id, platform_name=..., platform_username=..., metadata={"level": "12"})`. Значения метаданных — строки, как их принимает API Discord.

[Пример связанных ролей](../examples.md#linked-roles) — это готовая страница проверки. `fmt.guild_navigation("linked-roles", role_id)` даёт в чате ссылку на связанную роль сервера.
