---
source: 9b56e99b0e7750b300986ad6197431b73305caa0
---

# REST API

![공을 향해 뛰어오르는 Bostie](../assets/mascot/bostie-jumping.webp){ align=right width="180" loading=lazy }

`bot.rest`에는 봇이 호출할 수 있는 Discord REST API의 모든 작업마다 메서드가 하나씩 있으며, 작업 대상 리소스별로 묶여 있습니다. `bot.rest.messages.create`, `bot.rest.members.add_role`, `bot.rest.guilds.fetch`처럼 씁니다. 요청은 Discord의 레이트 리밋을 지키는 bostie의 Rust HTTP 클라이언트를 거칩니다.

```python
--8<-- "docs/snippets/rest/rest_calls.py"
```

## 규칙 { #conventions }

- URL에 들어가는 ID가 URL에 나오는 순서대로 먼저 오며, 위치 전용 인자입니다. `bot.rest.members.add_role(guild_id, user_id, role_id)`.
- 나머지는 모두 Discord 문서에 나온 이름 그대로의 키워드 인자이며, 작업마다 `TypedDict`로 검사합니다. 그래서 오타는 타입 에러가 되고, 실행 중에는 `TypeError`가 됩니다.
- Discord가 감사 로그에 기록하는 작업은 `reason=`을 받습니다. 1~512자이며, 서버의 모더레이터에게 표시됩니다.
- 이모지의 `image`나 서버의 `icon` 같은 이미지·사운드 필드는 `File`을 받으며, bostie가 Discord가 요구하는 data URI로 바꿔 보냅니다. 권한 필드는 `Permissions`를 받습니다.
- `reactions.create`가 추가하는 이모지처럼 이모지를 받는 곳에는 유니코드 이모지, `<:name:id>`, `<a:name:id>`, `name:id` 형식의 사용자 지정 이모지, 또는 `Emoji`나 `PartialEmoji` 모델을 넘깁니다.
- `fetch`라는 메서드는 객체 하나를 가져오고, `list`는 여러 개를 순회하며, `edit`는 수정, `create`는 생성, `delete`는 삭제합니다. 그 밖의 동사는 `members.add_role`이나 `guilds.begin_prune`처럼 문서의 단어를 그대로 씁니다.
- 메서드는 모델을 반환하며, Discord가 내용 없이 응답하면 `None`을 반환합니다. 메서드는 `bot.cache`를 바꾸지 않습니다. 다만 `channels.create_dm`은 `max_dm_channels`가 허용하면 DM 채널을 보관합니다. 캐시는 변경 뒤에 이어지는 게이트웨이 이벤트가 갱신합니다.

## 페이지 넘기기 { #paging }

Discord가 페이지로 나눠 주는 엔드포인트의 `list` 메서드는 비동기 이터레이터를 반환하며, 순회하는 동안 다음 페이지를 가져옵니다. 위의 `/today`처럼 `async for message in bot.rest.messages.list(channel_id, limit=500):`로 씁니다. `limit`이 없으면 모든 항목을 순회합니다. `before`, `after`, `around`는 다른 위치에서 시작하며, 이 중 하나만 쓸 수 있습니다. ID나 `datetime`을 받는데, `datetime`은 bostie가 그 시점의 ID로 바꿉니다. 메시지는 최신순으로 오고, `after`를 쓰면 오래된 순으로 옵니다. Discord가 페이지로 나누지 않는 엔드포인트의 `list` 메서드는 튜플을 반환하는 코루틴입니다. `audit_log.list`는 항목이 가리키는 사용자, 웹후크 등의 객체를 함께 담은 `AuditLogPage` 객체를 통째로 내놓습니다.

## 메시지 일괄 삭제 { #purging-messages }

`messages.purge(channel_id, limit=100, check=None)`는 최근 메시지 `limit`개를 최신순으로 훑으며 `check`가 참을 반환하는 메시지를 삭제하고, `check`가 없으면 모두 삭제합니다. `limit=None`이면 채널 전체를 훑으며, `before`와 `after`는 `list`처럼 다른 위치에서 시작합니다. Discord는 최근 메시지만 대량 삭제하므로, 14일이 지나지 않은 메시지는 최대 100개씩 대량 삭제하고, 그보다 오래된 메시지는 하나에 요청 하나씩 보냅니다. 스레드의 시작 메시지처럼 Discord가 삭제를 허용하지 않는 타입은 건너뜁니다. `bulk=False`를 주면 하나씩 삭제하는데, 대량 삭제가 없는 DM에서처럼 동작합니다. 삭제한 메시지를 반환하며, `reason`은 감사 로그에 남습니다. 대량 삭제와 다른 사람의 메시지 삭제에는 메시지 관리하기(Manage Messages) 권한이 필요합니다.

```python
--8<-- "docs/snippets/rest/purge.py"
```

## 감사 로그 { #the-audit-log }

`audit_log.list(guild_id)`는 `AuditLogPage` 객체를 내놓고, 게이트웨이는 새 항목마다 `AuditLogEntryCreate` 이벤트를 보냅니다. 항목의 `changes`에는 바뀐 필드마다 `AuditLogChange`가 하나씩 들어 있으며, 그 `new_value`와 `old_value`는 바뀐 객체가 그 필드에 쓰는 타입을 가집니다. 역할의 `permissions`는 `Permissions`, 멤버의 `communication_disabled_until`은 `datetime`, 채널의 `permission_overwrites`는 `Overwrite`의 튜플입니다. Discord의 예외는 문서를 따릅니다. 멤버 역할의 `$add`와 `$remove`에는 `AuditLogRole` 객체가 들어 있고, 웹후크의 `avatar_hash`와 초대의 `channel_id`는 각각 `avatar`의 타입과 ID 타입을 가지며, 명령어 권한 변경은 역할, 사용자 또는 채널 ID를 키로 하고 `CommandPermission`을 담습니다. bostie가 모르는 키나 필드가 받지 않는 모양의 값은 JSON 값을 그대로 유지하고, Discord가 생략한 값은 `None`입니다.

```python
--8<-- "docs/snippets/rest/audit_log.py"
```

## 에러 { #errors }

Discord가 에러 상태 코드로 응답한 요청은 `HTTPException`을 발생시킵니다. 400은 `BadRequest`, 403은 `Forbidden`, 404는 `NotFound`, 5xx는 `ServerError`입니다. 예외에는 `status`, `JSONErrorCode`로 표현한 Discord의 `code`, `message`, 필드별 `errors`, 원시 `body`가 있으며, 예외 텍스트는 필드 에러를 한 줄에 하나씩 나열합니다. `UnexpectedResponse`는 Discord가 요청을 받아들였지만 응답을 디코딩하지 못했다는 뜻입니다. 이 예외의 메시지는 변경이 적용되었는지 알려 주며, `error.raw`에 응답이 들어 있습니다.

## 레이트 리밋, 재시도, 타임아웃 { #rate-limits-retries-and-timeouts }

Rust 클라이언트는 라우트마다의 레이트 리밋 버킷과 Discord의 전역 제한을 추적하며, 버킷에 여유가 생길 때까지 요청을 붙잡아 둡니다. `Bot(http_settings=HTTPSettings(...))`로 동작을 바꿉니다.

| `HTTPSettings` 필드 | 기본값 | 의미 |
|---|---|---|
| `global_rate_limit` | 50 | 모든 라우트를 합친 초당 요청 수. Discord가 봇에 더 높은 제한을 줬다면 올립니다 |
| `max_rate_limit_wait` | 30.0 | 레이트 리밋으로 기다리는 최대 시간. 이보다 오래 기다려야 하는 요청은 보내지 않고 `RateLimited`를 발생시킵니다 |
| `retries` | 2 | 실패한 요청을 재시도하는 횟수. 지수 백오프로 기다리되 최대 `max_rate_limit_wait`까지 기다립니다 |
| `connect_timeout` | 10.0 | 연결을 여는 데 쓰는 시간(초) |
| `read_timeout` | 60.0 | 응답을 기다리는 시간(초) |
| `rate_limiter` | `True` | `False`면 즉시 보내고 돌아온 429만 기다립니다. 레이트 리밋 프록시 뒤에서 쓰기 위한 옵션입니다 |
| `proxy` | `None` | `voice.Url` 오디오와 CDN 다운로드를 포함한 모든 요청에 쓸 HTTP 또는 HTTPS 프록시 URL. 인증 정보는 `http://user:password@host:3128`처럼 넣습니다 |

429는 Discord가 알려 준 지연 시간이 `max_rate_limit_wait` 이내이면 그 시간 뒤에 재시도합니다. 500, 502, 503, 504, 524 응답이나 끊어진 연결은 이미 적용되었을 수 있는 `POST`를 제외한 모든 메서드에서 `retries`번 재시도합니다. 아예 열리지 않은 연결은 모든 메서드에서 재시도합니다.

Discord는 10분 동안 잘못된 요청(401, 403, 429)을 10 000개 보낸 IP를 차단합니다. bostie는 이를 세어 1000, 5000, 9000개에서 경고를 로그에 남깁니다. 9500개에 이르면 최근 11분 동안의 개수가 9000 아래로 내려갈 때까지 모든 요청을 보내지 않고 `RequestBudgetExhausted`로 거부합니다.

## 임의의 엔드포인트 { #any-endpoint }

`bot.http.request(method, route, **params)`는 같은 클라이언트로 어떤 요청이든 보내고 디코딩한 JSON을 반환하며, 응답이 비어 있으면 `None`을 반환합니다. 라우트에는 플레이스홀더를 그대로 두고, 키워드 인자로 채웁니다. `await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=123)`. `json=`, 멀티파트 업로드용 `files=`, 이름 있는 폼 필드용 `form=`, form-urlencoded 본문용 `urlencoded=`, `query=`, `reason=`을 받으며, 같은 예외를 발생시킵니다. `bot.http.send(...)`는 같은 인자를 받고 응답의 원시 바이트를 반환하므로, JSON으로 응답하지 않는 엔드포인트에 씁니다.

## 파일 다운로드 { #downloading-files }

첨부 파일과 이미지는 API 뒤가 아니라 Discord의 CDN에 있으므로, 다운로드할 때는 토큰을 보내지 않고 레이트 리밋도 쓰지 않습니다. `bot.http`는 같은 Rust 클라이언트로 이 파일들을 가져오며, 타임아웃, 재시도, `proxy` 설정도 그대로 적용됩니다.

| 메서드 | 동작 |
|---|---|
| `read(source)` | 바이트를 반환합니다 |
| `save(source, path)` | `path`의 파일에 씁니다 |
| `to_file(source)` | 다시 업로드할 `File`을 반환합니다. `Attachment`에서 만들면 파일 이름, 설명, 제목, 스포일러, 음성 메시지 정보를 유지하고, URL에서 만들면 경로의 마지막 부분을 이름으로 씁니다 |

`source`는 `Attachment`이거나, [`bostie.cdn`](formatting.md) 헬퍼가 만드는 것과 같은 URL입니다. 파일이 없으면 `NotFound`를 발생시킵니다.

```python
--8<-- "docs/snippets/rest/downloads.py"
```

## 봇 없이 쓰기 { #without-a-bot }

REST만 필요한 스크립트나 웹 백엔드는 `RESTClient`를 씁니다.

```python
--8<-- "examples/rest_script.py"
```

`async with RESTClient(token) as client:`가 HTTP 클라이언트를 엽니다. `client.rest`에는 `bot.rest`의 모든 그룹이 있고, `client.http`는 원시 클라이언트입니다. 토큰의 기본값은 `BOSTIE_TOKEN`이며, 클라이언트는 봇처럼 `api_url`, `http_settings`, `allowed_mentions`를 받습니다. 사용자의 토큰으로 API를 호출하는 `BearerClient`는 [OAuth2 가이드](oauth2.md)에서, 토큰이 전혀 필요 없는 클라이언트는 [웹후크 가이드](webhooks.md)에서 다룹니다.

## 그룹 { #the-groups }

| `bot.rest.` | 메서드 |
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

각 메서드의 매개변수와 반환 타입은 [REST API 레퍼런스](../reference/api/rest.md)에 있습니다. 몇몇 작업은 일부러 `bot.http.request`에 맡겼습니다. Discord가 지원 중단했거나 문서화한 적 없는 엔드포인트, 그리고 [인터랙션 컨텍스트](responses.md)가 다루는 인터랙션 콜백과 원래 응답(original response) 엔드포인트가 그렇습니다.

일부 그룹은 서버의 봇을 넘어서는 Discord 기능을 다룹니다. `monetization`과 `application`은 [수익화와 활동](platform.md)에, `lobbies`, `identities`, `partner_sdk`는 [Social SDK](platform.md#social-sdk)에, `application.update_role_connection_metadata`는 [연결된 역할](oauth2.md#linked-roles)에 쓰입니다.
