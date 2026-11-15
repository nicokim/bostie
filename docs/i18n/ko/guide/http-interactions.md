---
source: 04dd5c7d848eed6b7fe26c0af24d76fdfca1a0d2
---

# HTTP 인터랙션과 웹후크 이벤트

Discord는 인터랙션을 게이트웨이 대신 앱의 URL로 보낼 수 있으며, 웹후크 이벤트라는 일부 이벤트는 URL로만 보냅니다. `ASGIApp`은 같은 봇으로 둘 다 처리하며, 봇의 명령어, 라우트, 리스너는 바꿀 필요가 없습니다.

```python
--8<-- "docs/snippets/http/http_bot.py"
```

`bostie.ASGIApp(bot)`는 ASGI 애플리케이션입니다. 아무 ASGI 서버로나 `app`을 HTTPS 뒤에서 제공하세요. 그런 다음 Developer Portal에서 앱의 **Interactions Endpoint URL**을, 웹후크 이벤트를 쓴다면 **Webhook Events URL**과 받을 이벤트 타입을, 앱이 요청을 받는 주소로 설정합니다. Discord는 엔드포인트를 저장할 때 PING과 서명이 틀린 요청으로 엔드포인트를 확인하며, `ASGIApp`은 둘 다 Discord가 기대하는 대로 응답합니다.

## 동작 방식 { #what-it-does }

서버가 시작되면 앱은 ASGI lifespan을 통해, 봇이 이미 게이트웨이에서 실행 중이 아니라면 `bot.open()`을 호출합니다. `bot.open()`은 다음을 수행합니다.

1. 봇의 HTTP 클라이언트를 엽니다.
2. 애플리케이션 정보를 가져와 ID와 Discord 요청에 서명하는 공개 키를 얻고, 봇의 사용자 정보도 가져옵니다.
3. `Starting`을 보내고, 명령어를 동기화하고, [백그라운드 태스크](tasks.md)를 시작합니다.

여기서 실패하면 서버 시작이 그 에러와 함께 중단됩니다. 서버가 종료될 때, 봇을 앱이 시작했다면 앱이 봇을 닫습니다.

모든 요청은 무엇보다 먼저 검사합니다. `POST`만 받고(그 외에는 405), 본문은 4 MiB로 제한하며(413), `X-Signature-Ed25519`가 공개 키로 검증되지 않는 요청에는 401을 반환합니다. 그다음은 이렇습니다.

- 인터랙션 PING에는 Discord가 기대하는 PONG을 반환합니다.
- 그 밖의 인터랙션에는 202로 응답하고 봇에 넘깁니다. 봇은 게이트웨이에서와 마찬가지로 명령어, 라우트, 모달 핸들러를 실행하며, 핸들러는 `ctx.respond` 등으로 Discord의 REST API를 통해 응답합니다. [자동 응답 지연](responses.md#deferring)도 똑같이 동작합니다.
- 웹후크 이벤트는 봇의 리스너에 전달되고 204를 받습니다. Discord가 Webhook Events URL로 보내는 PING도 마찬가지입니다.

`ASGIApp(bot, token=..., public_key=...)`는 토큰을 받으며 없으면 `BOSTIE_TOKEN`을 쓰고, 공개 키를 받으며 없으면 애플리케이션이 알려 주는 키를 씁니다.

## 게이트웨이와 함께 또는 없이 { #with-or-without-the-gateway }

앱만 단독으로 제공하면 게이트웨이 연결이 없으므로 게이트웨이 이벤트, 캐시 갱신, 음성을 쓸 수 없습니다. 명령어, 컴포넌트, 모달, REST, 웹후크 이벤트는 동작합니다. 둘 다 쓰려면 같은 이벤트 루프에서 봇을 게이트웨이로도 시작하세요. 그러면 앱은 실행 중인 봇을 사용하고, 인터랙션은 HTTP로 도착하며, 게이트웨이는 계속 이벤트를 전달합니다.

## 웹후크 이벤트 { #webhook-events }

| 이벤트 | 보내는 시점 |
|---|---|
| `ApplicationAuthorized` | 사용자가 앱을 서버나 자기 계정에 추가했을 때 |
| `ApplicationDeauthorized` | 사용자가 앱을 제거했거나, 앱의 OAuth2 토큰이 만료되었을 때 |
| `EntitlementCreate`, `EntitlementUpdate`, `EntitlementDelete` | 엔타이틀먼트가 바뀌었을 때 |
| `QuestUserEnrollment` | 사용자가 퀘스트에 등록되었을 때. Discord가 문서화했지만 아직 앱에 보내지는 않습니다 |
| `LobbyMessageCreate`, `LobbyMessageUpdate`, `LobbyMessageDelete` | Social SDK 로비에서 메시지가 전송, 수정, 삭제되었을 때 |
| `GameDirectMessageCreate`, `GameDirectMessageUpdate`, `GameDirectMessageDelete` | Social SDK 세션의 개인 메시지가 바뀌었을 때 |

게이트웨이 이벤트처럼 `@bot.listen`으로 받습니다. bostie가 모델링하지 않은 웹후크 이벤트 타입은 `RawEvent`로 도착합니다.

## 직접 운영하는 웹 서버에서 { #in-your-own-web-server }

`ASGIApp`이 쓰는 두 구성 요소는 공개되어 있어, 이미 운영 중인 웹 프레임워크에서 쓸 수 있습니다.

```python
--8<-- "docs/snippets/http/own_server.py"
```

`verify_signature(public_key, signature, timestamp, body)`는 요청의 `X-Signature-Ed25519`와 `X-Signature-Timestamp` 헤더를 앱의 16진수 공개 키로 검사합니다. `False`를 반환하면 401로 응답하세요. `decode_webhook_event(body)`는 Webhook Events 요청의 타입이 지정된 이벤트를 반환하며, bostie가 모델링하지 않은 타입이면 `RawEvent`를, PING이면 `None`을 반환합니다. 직접 운영하는 서버에서 인터랙션을 처리하는 방법은 [HTTP 인터랙션 예제](../examples.md#http-interactions)가 ASGI 방식으로 보여 주며, bostie가 지원하는 방식은 이것입니다.
