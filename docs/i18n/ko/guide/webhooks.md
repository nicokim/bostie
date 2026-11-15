---
source: e4d65a29086e1f3e4e82a64b8aa6c42d29627ef0
---

# 웹후크

웹후크는 자체 이름과 아바타로 한 채널에 메시지를 올립니다. 웹후크 URL에는 ID와 토큰이 들어 있고, 메시지를 올리는 데는 토큰만 있으면 되므로, 배포 스크립트나 모니터링 작업이 봇 없이도 메시지를 보낼 수 있습니다.

```python
--8<-- "docs/snippets/webhooks/deploy_notice.py"
```

`WebhookClient(url)`는 웹후크 URL에서 ID와 토큰을 파싱하며, 웹후크 URL이 아니면 `ValueError`를 발생시킵니다. HTTP 클라이언트를 열고 닫아 주는 `async with` 안에서 사용하세요. 이벤트 루프가 없는 코드에는 같은 메서드를 가진 `SyncWebhookClient(url)`가 있으며, 호출할 때마다 자체 이벤트 루프를 실행합니다.

| 메서드 | 동작 |
|---|---|
| `execute(content, **parts)` | 메시지를 올리고, Discord가 저장하면 그 메시지를 반환합니다 |
| `edit_message(message_id, **parts)` | 웹후크가 보낸 메시지를 수정합니다. 생략한 부분은 그대로 두고, `None`은 지웁니다 |
| `fetch_message(message_id)`, `delete_message(message_id)` | 메시지 하나를 가져오거나 삭제합니다 |
| `fetch()`, `edit(name=..., avatar=...)`, `delete()` | 웹후크 자체를 다룹니다 |
| `execute_slack(payload)`, `execute_github(payload)` | Discord의 Slack 호환, GitHub 호환 엔드포인트가 받는 형식의 페이로드를 올립니다 |

`execute`는 [메시지 구성 요소](messages.md)를 받고, 그 밖에 다음도 받습니다.

- `username`과 `avatar_url`: 이 메시지에서만 웹후크 자체의 이름과 아바타 대신 표시됩니다.
- `thread_id`: 채널의 스레드에 올립니다. 메시지 메서드들도 이 인자를 받습니다.
- `thread_name`: 포럼 채널이나 미디어 채널에 스레드를 시작합니다. `applied_tags`와 함께 씁니다.
- `with_components=True`: 앱이 소유하지 않은 웹후크에서 컴포넌트를 쓰려면 필요합니다.

클라이언트는 봇처럼 `api_url`, `http_settings`, `allowed_mentions`를 받습니다. 기본적으로 웹후크는 사용자와 역할에 알림을 보낼 수 있지만 `@everyone`에는 보낼 수 없습니다.

Discord가 404 Unknown Webhook으로 응답하면 웹후크가 삭제된 것입니다. 그러면 bostie는 그 웹후크에 대한 이후의 모든 요청을 보내지 않고 같은 404로 응답하므로, 삭제된 웹후크는 [잘못된 요청 제한](rest.md#rate-limits-retries-and-timeouts)에 포함되지 않습니다.

## 봇으로 웹후크 관리하기 { #managing-webhooks-with-a-bot }

`bot.rest.webhooks`는 채널이나 서버의 웹후크를 만들고 나열하며(웹후크 관리하기(Manage Webhooks) 권한이 필요합니다), 토큰을 쓰는 메서드도 갖추고 있습니다.

- `create(channel_id, name=..., avatar=File(...))`는 `Webhook`을 반환하며, 그 `url`을 `WebhookClient`에 넘길 수 있습니다.
- `list(channel_id)`, `list_guild(guild_id)`, `fetch(webhook_id)`, `edit(webhook_id, ...)`, `delete(webhook_id)`는 봇의 토큰으로 동작합니다.
- `execute(webhook_id, token, ...)`와 그 밖의 `_with_token` 메서드 및 메시지 메서드는 `WebhookClient`가 호출하는 메서드입니다.

`WebhooksUpdate` 이벤트는 채널의 웹후크가 바뀌었음을 알립니다. 웹후크가 보낸 메시지는 `WebhookMessage`로 도착하며, 그 `author`는 `PartialUser`이고 `webhook_id`가 설정되어 있습니다.
