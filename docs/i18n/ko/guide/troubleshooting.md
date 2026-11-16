---
source: e7704fd7192c52cc55342cad633590bd6f185904
description: "로그 줄로 bostie 문제를 해결합니다. \"The application did not respond\", 오지 않는 이벤트, 게이트웨이, REST, 음성 문제."
---

# 문제 해결

![물음표가 전구로 바뀔 때까지 고민하는 Bostie](../assets/mascot/bostie-thinking.webp){ align=right width="180" loading=lazy }

bostie는 감지할 수 있는 실수마다 경고를 로그로 남깁니다. 이 페이지에서는 겪는 증상별로, 찾아볼 로그 줄과 해결 방법을 정리합니다.

## 명령어에 "The application did not respond"가 표시될 때 { #a-command-says-the-application-did-not-respond }

| 로그 줄 | 원인 | 해결 방법 |
|---|---|---|
| `nothing handles interaction ... (command 'name')` | 실행 중인 봇이 정의하지 않은 명령어가 Discord에 아직 남아 있습니다. `auto_sync=False` 상태에서 이름을 바꾼 뒤에 자주 생기며, 명령어가 다른 프로세스에 있는 경우일 수도 있습니다 | 명령어를 동기화합니다([명령어 등록](registration.md)) |
| `nothing handles interaction ... (custom_id '...')` | 커스텀 ID와 일치하는 `on_component`나 `on_modal` 템플릿이 없습니다 | 보내는 ID와 템플릿을 대조하고, ID는 `route.id(...)`로 만듭니다 |
| `... returned without answering` | 핸들러가 `respond`, `defer`, `modal` 없이 끝났습니다 | 중간에 return하는 경로를 포함해 모든 경로에서 응답합니다 |
| `... returned after deferring without answering` | 응답을 지연한 뒤 끝내 응답하지 않아서, 토큰이 만료될 때까지 "thinking…"이 남아 있습니다 | 오래 걸리는 작업 뒤에 `respond`를 호출합니다 |
| `interaction ... reached its handler ... s after it was received; something is blocking the event loop` | 핸들러나 리스너의 블로킹 코드가 Discord의 3초 제한을 넘겨 이벤트 루프를 붙잡았습니다 | 작업을 `asyncio.to_thread`로 옮기거나 `auto_defer`를 낮춥니다 |
| `interaction ... was replayed ... s after it was created; its token is dead` | 재개한 세션의 재전송으로 인터랙션이 들어와서, 응답하기에 너무 늦었습니다 | 할 일은 없습니다. 사용자가 다시 실행하면 됩니다 |

핸들러에서 처리되지 않은 예외는 트레이스백과 함께 로그에 남고, 사용자에게는 "Something went wrong."이 표시됩니다. 더 나은 응답을 하려면 [에러 훅](checks.md#error-hooks)을 추가합니다.

## 이벤트가 오지 않을 때 { #events-do-not-arrive }

- `... listens to EVENT, which needs the INTENT intent; add it to Bot(intents=...)`: 봇이 해당 인텐트를 요청하지 않았습니다. 인텐트를 추가하고, 특권 인텐트라면 Developer Portal에서도 켭니다.
- `start()`가 `PrivilegedIntentsRequired`를 발생시킵니다: 포털에서 허용하지 않은 특권 인텐트를 봇이 요청했습니다. **Bot** → **Privileged Gateway Intents**에서 켜거나 `intents`에서 제거합니다.
- 서버에서 `message.content`가 비어 있습니다: `MESSAGE_CONTENT` 인텐트가 빠져 있습니다.
- `EVENT has an unexpected shape (...); typed listeners skip it`: 이벤트 클래스에 맞지 않는 페이로드를 Discord가 보냈으며, 이벤트 이름마다 한 번씩 로그에 남습니다. `RawEvent` 리스너는 여전히 이 페이로드를 받습니다. 이 로그 줄을 제보해 주세요.
- 업데이트 이벤트에서 `old`가 `None`입니다: 캐시에 해당 객체가 없었습니다. 캐시가 무엇을 보관하는지는 [캐시](cache.md#what-it-keeps)를 참고하세요.

## 게이트웨이 { #the-gateway }

- `shard N paused reading: ... bytes of events are waiting because the event loop is not keeping up`: 무언가가 이벤트 루프를 막고 있습니다. [백프레셔](gateway.md#backpressure)를 참고하세요.
- `close code N (...) means Discord refused what bostie sent; this is likely a bostie bug, please report it`: 종료 코드 4001, 4002, 4005, 4008은 bostie가 보낸 것을 Discord가 거부했다는 뜻입니다.
- `the members of guild N stopped arriving; the cache lacks some`: 캐시용 멤버 요청이 청크당 30초를 넘겨 타임아웃되었습니다.
- `start()`가 `InvalidToken`을 발생시킵니다: 토큰이 틀렸거나 재설정되었습니다. **Bot** 페이지에서 다시 복사합니다.

## REST { #rest }

- `N invalid requests (401, 403, 429) in the last 10 minutes; Discord bans the IP at 10 000`: 봇이 Discord가 거부하는 요청을 계속 보내고 있습니다. 대개 권한이 없거나 웹후크가 사라진 경우입니다. 실패하는 호출은 그 호출이 발생시키는 예외에서 찾습니다.
- 요청이 Discord에 닿지 않았는데 `RateLimited`가 발생합니다: 라우트의 제한이 `max_rate_limit_wait`보다 오래 유지됩니다. [`HTTPSettings`](rest.md#rate-limits-retries-and-timeouts)에서 값을 늘리거나 나중에 다시 시도합니다.

## 응답 { #answers }

- `ephemeral=... is ignored because the message being edited is ...`: 메시지는 보낸 뒤에 에페메랄과 공개 사이를 오갈 수 없습니다.
- `Discord allows 5 follow-ups to an interaction of an app installed only to the user`: 사용자 설치 앱의 여섯 번째 후속 메시지는 실패합니다.
- `the error hook ... left interaction ... unanswered; bostie answered 'Something went wrong.'`: 에러 훅이 응답하도록 만듭니다.

## 음성 { #voice }

- `nobody hears the bot in guild N: it is server muted` 또는 `it is suppressed on this stage; use vc.stage('request')`: 모더레이터가 봇을 음소거했거나, 봇이 스테이지의 청중입니다.
- `a moderator deafened the bot in guild N, so Discord sends it no audio`: 헤드셋 음소거가 풀릴 때까지 리시버는 아무것도 받지 못합니다.
- 이유가 `TIMEOUT`인 `VoiceConnectError`: Discord가 `timeout`초 안에 참가 요청에 응답하지 않았습니다. 채널이 가득 찼거나, 봇에 연결(Connect) 권한이 없을 수 있습니다.
- `MissingIntent: voice needs Intents.GUILD_VOICE_STATES`: 음성에는 이 인텐트가 필요하며, `Intents.DEFAULT`에 포함되어 있습니다.

## 버그 제보하기 { #reporting-a-bug }

`python -m bostie`는 함께 적어야 할 버전을 출력하고, 음성 문제라면 `bostie.voice.versions()`가 libopus와 DAVE 버전을 더해 줍니다. [로깅](running.md#logging)에서 보여 주듯 봇을 시작하기 전에 관련 로거를 `DEBUG`로 설정하고, 토큰을 지운 상태로 문제 전후의 로그 줄을 포함해 주세요.
