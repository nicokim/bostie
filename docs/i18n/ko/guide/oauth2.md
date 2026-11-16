---
source: a95f1211f80aa1fe37d568699ddca680b3c3ed9d
description: "bostie로 Discord OAuth2를 씁니다. PKCE 인증 URL, 코드 교환과 갱신, 사용자 토큰으로 API 호출, 연결된 역할 메타데이터를 다룹니다."
---

# OAuth2와 연결된 역할

![반짝이는 열쇠를 들어 올린 Bostie](../assets/mascot/bostie-oauth2.webp){ align=right width="180" loading=lazy }

OAuth2를 쓰면 사용자가 앱에 자기 계정에 대한 접근 권한을 줄 수 있습니다. 프로필, 참여한 서버, 연결된 계정, 사용자를 서버에 추가할 권리, 연결된 역할에 쓰는 역할 연결 등이 그 대상입니다. bostie는 인증 URL을 만들고, 사용자가 허가한 토큰으로 API를 호출합니다.

```python
--8<-- "docs/snippets/oauth2/oauth_flow.py"
```

## 인증 URL { #authorization-urls }

`oauth2.authorize_url(client_id, scopes=[...], redirect_uri=..., state=...)`는 사용자에게 `scopes`를 허가해 달라고 요청하고, `code`와 함께 `redirect_uri`로 리디렉션하는 URL을 반환합니다. 스코프는 `OAuth2Scope` 멤버이며(예: `identify`에 해당하는 `OAuth2Scope.IDENTIFY`), Discord가 문서화한 스코프마다 하나씩 있습니다. bostie가 아직 이름을 붙이지 않은 새 스코프에는 일반 문자열을 쓸 수 있습니다. 리디렉션에 보낸 `state`가 그대로 들어 있는지 확인하세요. 그래야 다른 사이트가 자기 코드로 사용자를 로그인시키지 못합니다. 이 함수는 다음도 받습니다.

- `code_challenge`: PKCE verifier의 S256 challenge입니다. 이후 `exchange_code`가 이를 `code_verifier`로 보냅니다. 예제 코드에서 계산하는 방법을 보여 줍니다.
- `prompt="none"`: 이미 스코프를 허가한 사용자에게 동의 화면을 건너뜁니다. `"consent"`를 주면 동의 화면을 다시 보여 줍니다.
- `integration_type`: 앱을 서버에 설치할지 사용자에게 설치할지 정합니다.

bostie는 인증 코드 그랜트(authorization code grant)만 만듭니다. Discord는 리디렉션 URL에 토큰을 넣고 리프레시 토큰을 주지 않는 암시적 그랜트(implicit grant)를 권장하지 않으며, 대신 PKCE를 쓰는 코드 그랜트를 권장합니다.

`oauth2.invite_url(client_id, permissions=..., scopes=[...])`는 앱의 설치 링크를 반환하며, `integration_type`, 서버를 미리 선택하는 `guild_id`, `disable_guild_select`도 받습니다. 지정하지 않은 값은 Developer Portal의 앱 설치 설정을 따릅니다.

## 토큰 { #tokens }

토큰 없이 만든 `BearerClient()`로는 `rest.oauth2`만 쓸 수 있습니다.

| 메서드 | 동작 |
|---|---|
| `exchange_code(client_id=, client_secret=, code=, redirect_uri=, code_verifier=None)` | 리디렉션으로 받은 코드를 `AccessToken`으로 바꿉니다 |
| `refresh(client_id=, client_secret=, refresh_token=)` | 기존 토큰이 만료되기 전에 새 토큰을 받습니다 |
| `revoke(client_id=, client_secret=, token=)` | 토큰과 그 토큰이 발급한 모든 토큰의 인증을 끝냅니다 |
| `exchange_client_credentials(client_id=, client_secret=, scopes=[...])` | 앱 소유자 또는 그 팀을 위한 토큰을 받습니다 |

`AccessToken`에는 `access_token`, `token_type`, 초 단위의 `expires_in`, `scope`(허가된 스코프를 공백으로 구분한 값), `refresh_token`이 있습니다. `bot` 스코프를 포함한 코드 그랜트는 봇이 참여한 `guild`도 반환하고, `webhook.incoming`을 포함하면 만들어진 `webhook`도 반환합니다. 토큰의 `repr`은 비밀 값을 숨기므로, 토큰을 로그에 남겨도 유출되지 않습니다.

## 사용자로서 API 호출하기 { #calling-the-api-as-the-user }

`async with BearerClient(access_token) as user:`로는 토큰의 스코프가 허용하는 기능을 쓸 수 있습니다.

| `user.rest.` | 메서드 | 스코프 |
|---|---|---|
| `users` | `fetch_current` | `identify` |
| `users` | `list_connections` | `connections` |
| `users` | `fetch_role_connection`, `update_role_connection`, `delete_role_connection` | `role_connections.write` |
| `guilds` | `list` | `guilds` |
| `members` | `fetch_current(guild_id)` | `guilds.members.read` |
| `commands` | `fetch_permissions`, `list_permissions`, `edit_permissions` | 수정하려면 `applications.commands.permissions.update` |
| `oauth2` | 토큰이 무엇을 누구에게 허가하는지 알려 주는 `fetch_authorization` | 아무 스코프 |
| `lobbies` | [Social SDK 로비](platform.md#social-sdk)의 사용자 쪽 기능 | `sdk.social_layer` |

`guilds.join` 스코프가 있으면 봇은 자신이 들어가 있는 서버에 사용자를 추가할 수 있습니다. `await bot.rest.members.add(guild_id, user_id, access_token=token.access_token)`는 새 `Member`를 반환하며, 사용자가 이미 멤버였으면 `None`을 반환합니다.

## 연결된 역할 { #linked-roles }

연결된 역할은 사용자가 앱과의 연결에서 "레벨 10 이상" 같은 조건을 충족하면 받는 서버 역할입니다. 앱은 보고할 값을 한 번 선언해 두고, 그다음 사용자마다 그 사용자의 토큰으로 값을 보고합니다.

1. `bot.rest.application.update_role_connection_metadata(application_id, [...])`로 메타데이터 레코드를 최대 5개 등록합니다. 각 레코드는 `type`, `key`, `name`, `description`을 가진 `ApplicationRoleConnectionMetadata`입니다. 예제 코드의 `register_metadata`가 `RESTClient`로 이 작업을 합니다.
2. Developer Portal에서 앱의 **Linked Roles Verification URL**을 `role_connections.write` 스코프로 OAuth2 흐름을 시작하는 페이지로 설정합니다.
3. 코드 교환 뒤에 `user.rest.users.update_role_connection(application_id, platform_name=..., platform_username=..., metadata={"level": "12"})`를 호출합니다. 메타데이터 값은 Discord API가 받는 대로 문자열입니다.

[연결된 역할 예제](../examples.md#linked-roles)는 완성된 인증 페이지입니다. `fmt.guild_navigation("linked-roles", role_id)`는 채팅에서 서버의 연결된 역할로 가는 링크를 만듭니다.
