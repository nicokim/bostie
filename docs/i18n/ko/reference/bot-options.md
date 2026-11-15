---
source: 104f50c5749c73fc5952a47fa18309cb11805eb3
---

# Bot 옵션

`Bot(...)`의 모든 옵션이며, 모두 키워드 전용입니다. 각 옵션은 가이드에서 사용 맥락과 함께 설명합니다.

## 게이트웨이와 식별 정보 { #gateway-and-identity }

| 옵션 | 기본값 | 의미 |
|---|---|---|
| `intents` | `Intents.DEFAULT` | 요청할 [인텐트](../guide/gateway.md#intents). 기본값에는 특권 인텐트 세 개를 뺀 모든 인텐트가 들어 있습니다 |
| `status` | `Status.ONLINE` | 로그인할 때의 [프레즌스](../guide/gateway.md#presence) 상태 |
| `activities` | `()` | 로그인할 때의 활동. `bostie.activity`로 만듭니다 |
| `afk` | `False` | 봇이 자리 비움 상태임을 알릴지 여부 |
| `idle_since` | `None` | 봇이 유휴 상태가 된 `datetime` |
| `large_threshold` | 50 | 50에서 250 사이. 서버의 멤버 수가 이 값을 넘으면 Discord가 서버 페이로드에서 오프라인 멤버를 뺍니다 |
| `shard_count` | `"auto"` | [샤드](../guide/sharding.md) 수. `"auto"`이면 Discord의 권장값을 씁니다 |
| `shard_ids` | `"all"` | 이 프로세스가 실행할 샤드. `shard_count`를 명시해야 합니다 |
| `before_identify` | `None` | 각 identify 전에 await하는 `async def f(shard_id)`. 프로세스 간 조율에 씁니다 |
| `session_file` | `None` | `close()`가 세션을 저장하고 다음 `start()`가 그 세션을 [재개](../guide/sharding.md#resuming-after-a-restart)하는 경로 |
| `guild_ready_timeout` | 2.0 | 새 서버가 이 시간(초) 동안 들어오지 않으면 샤드를 [준비 완료](../guide/cache.md#startup)로 간주합니다 |

## 명령어와 인터랙션 { #commands-and-interactions }

| 옵션 | 기본값 | 의미 |
|---|---|---|
| `auto_sync` | `True` | 시작할 때 명령어가 Discord의 것과 다르면 [명령어를 동기화](../guide/registration.md)합니다 |
| `dev_guilds` | `()` | 명령어를 전역 대신 등록할 서버 ID |
| `contexts` | `None` | 명령어나 라우터가 따로 지정하지 않을 때 전역 명령어를 사용할 수 있는 위치 |
| `integration_types` | `None` | 명령어나 라우터가 따로 지정하지 않을 때 전역 명령어가 포함되는 설치 유형 |
| `translate` | `None` | [번역](../guide/registration.md#translations) 또는 `None`을 반환하는 `f(text, locale)` |
| `auto_defer` | 2.5 | 응답하지 않은 인터랙션을 [응답 지연](../guide/responses.md#deferring)하기까지의 시간(초). `None`이면 끕니다 |
| `allowed_mentions` | `AllowedMentions(parse=("users", "roles"))` | 기본적으로 [알림을 보낼 수 있는](../guide/responses.md#allowed-mentions) 멘션 |
| `owner_ids` | `None` | [`is_owner`](../guide/checks.md#roles-and-owners)가 통과시키는 사용자. `None`이면 앱의 소유자나 팀을 읽어 옵니다 |

## 캐시 { #cache }

| 옵션 | 기본값 | 의미 |
|---|---|---|
| `cache` | `True` | [캐시](../guide/cache.md)를 유지합니다. `False`이면 아무것도 보관하지 않습니다 |
| `members` | `False` | 모든 멤버를 보관합니다. `Intents.GUILD_MEMBERS`가 필요합니다 |
| `chunk_members` | `True` | `members=True`일 때, 시작 시 Discord가 빼고 보낸 멤버를 요청합니다 |
| `presences` | `False` | 프레즌스를 보관합니다. `Intents.GUILD_PRESENCES`가 필요합니다 |
| `max_messages` | 0 | 보관할 최근 메시지 수 |
| `max_dm_channels` | 0 | 보관할 최근 개인 메시지(DM) 채널 수 |

## HTTP, 음성, 종료 { #http-voice-and-shutdown }

| 옵션 | 기본값 | 의미 |
|---|---|---|
| `http_settings` | `HTTPSettings()` | REST 요청의 [레이트 리밋, 재시도, 타임아웃, 프록시](../guide/rest.md#rate-limits-retries-and-timeouts) |
| `api_url` | `None` | REST 기본 URL. 없으면 `BOSTIE_API_URL`, 그것도 없으면 Discord의 URL을 씁니다 |
| `voice` | `voice.Defaults()` | 모든 연결의 출발점이 되는 [음성 설정](../voice/index.md#settings) |
| `shutdown_grace` | 0.0 | [`close()`](../guide/running.md#the-lifecycle)가 실행 중인 핸들러를 취소하기 전에 기다려 주는 시간(초) |

`run(token=None, loop_factory=None)`과 `start(token=None)`은 토큰을 받으며, 기본값은 `BOSTIE_TOKEN`입니다.
