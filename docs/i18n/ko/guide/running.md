---
source: fecb554b875bf1b19567e85926b3784beca4dba5
---

# 봇 실행하기

![색종이를 뿌리며 축하하는 Bostie](../assets/mascot/bostie-celebrating.webp){ align=right width="180" loading=lazy }

## `bot.run()` { #botrun }

대부분의 봇에서는 `bot.run()`이 프로그램의 전부입니다. 루트 로거에 핸들러가 아직 없으면 stderr에 INFO 레벨로 로깅을 설정하고, 이벤트 루프를 시작한 뒤, 봇이 멈출 때까지 실행합니다. Ctrl+C와, 메인 스레드에서 받은 SIGTERM은 봇을 깔끔하게 종료합니다.

토큰은 `BOSTIE_TOKEN` 환경 변수나 `bot.run(token)`에서 가져옵니다. 맨 앞의 `Bot `은 제거되며, 토큰이 없으면 `ValueError`가 발생합니다. `run`은 이벤트 루프를 만드는 함수인 `loop_factory`도 받으며, 이 함수는 `asyncio.run`에 그대로 전달됩니다.

## 수명 주기 { #the-lifecycle }

`run()` 뒤에서 실제 작업을 하는 것은 `bot.start()`입니다.

1. `Starting` 이벤트를 보내고 각 리스너를 차례로 await합니다. 이 단계에서 REST는 동작하지만 게이트웨이는 동작하지 않습니다. 리스너가 예외를 일으키면 시작이 중단됩니다.
2. `auto_sync=False`가 아니면 [명령어를 동기화](registration.md)합니다.
3. 게이트웨이 샤드를 연결합니다. 모든 샤드가 준비되고 각 샤드의 서버가 도착하면 `Started`를 한 번 보내고, [백그라운드 태스크](tasks.md)를 시작하며, `wait_until_ready()`가 반환되게 합니다.
4. `close()`가 호출되거나 Discord가 게이트웨이를 영구히 닫을 때까지 이벤트를 처리합니다. 후자의 경우 `InvalidToken`이나 `PrivilegedIntentsRequired` 같은 예외가 발생합니다.

```python
--8<-- "docs/snippets/running/lifecycle_events.py"
```

`await bot.close()`는 다음 순서로 종료합니다.

1. `Started`를 보냈다면 `Stopping`을 보내고, 게이트웨이가 아직 열려 있는 동안 리스너에게 최대 5초를 줍니다.
2. 모든 음성 채널에서 나간 다음 각 샤드를 닫습니다. 이때 세션을 close code 1000으로 끝내거나, [재개](sharding.md#resuming-after-a-restart)를 위해 저장합니다.
3. 백그라운드 태스크를 취소합니다. 아직 실행 중인 핸들러는 `shutdown_grace`초(기본값 0) 동안 기다린 뒤 마찬가지로 취소됩니다.
4. HTTP 클라이언트를 닫고 `Stopped`를 보내며, 그 리스너를 await합니다. 이후 `bot.http`, `bot.rest`, `bot.gateway`는 더 이상 쓸 수 없습니다.

`close()`는 어디서든, 여러 번, 그리고 `start()`가 실행 중일 때도 호출할 수 있으며, 그 뒤에 봇을 다시 시작할 수 있습니다. `async with bot:`은 블록이 끝날 때 봇을 닫습니다.

## 다른 프로그램 안에서 실행하기 { #inside-another-program }

프로그램이 이미 이벤트 루프를 실행하고 있다면 봇을 태스크로 시작합니다.

```python
--8<-- "docs/snippets/running/inside_asyncio.py"
```

`await bot.wait_until_ready()`는 `Started`가 전송되면 반환하고, 이미 전송된 뒤라면 바로 반환하며, 그 전에 봇이 멈추면 `StateError`를 일으킵니다. 이 경우 `start()` 태스크를 await하면 멈춘 이유가 예외로 발생합니다. `bot.is_ready`는 기다리지 않고 같은 정보를 알려 줍니다. [웹 앱 예제](../examples.md#web-app)는 웹 서버의 lifespan 안에서 봇을 실행하고 웹 라우트에서 메시지를 보냅니다.

HTTP로 인터랙션에만 응답하는 봇은 게이트웨이가 필요 없습니다. [HTTP 인터랙션](http-interactions.md)을 참고하세요. `await bot.open()`은 봇을 그런 방식으로 시작하고 반환합니다. HTTP 클라이언트를 열고, 애플리케이션과 봇의 사용자를 가져오고, `Starting`을 보내고, 명령어를 동기화하고, 백그라운드 태스크를 시작한 뒤 애플리케이션을 반환합니다. `ASGIApp`이 이 메서드를 호출하며, `close()`가 이를 끝냅니다. REST API만 호출하는 스크립트는 [`RESTClient`](rest.md#without-a-bot)를 사용합니다.

## 실행 중에 쓸 수 있는 속성 { #while-it-runs }

| 속성 | 값 |
|---|---|
| `bot.user` | 연결된 뒤의 봇의 `CurrentUser` |
| `bot.application_id` | 시작된 뒤의 앱 ID |
| `bot.latency` | 샤드들의 평균 하트비트 왕복 시간(초) 또는 `None` |
| `bot.is_running` | `start()`나 `open()`이 봇을 실행 중인지 여부. `close()`로 끝날 때까지 유지됩니다 |
| `bot.is_ready` | 마지막 `start()` 이후 `Started`가 전송되었는지 여부 |
| `bot.rest`, `bot.http` | 시작된 뒤의 [REST API](rest.md) |
| `bot.gateway` | 연결된 뒤의 [게이트웨이 샤드](gateway.md#raw-gateway-commands) |
| `bot.cache` | [캐시](cache.md) |
| `bot.voice` | [음성 연결](../voice/index.md) |
| `bot.auto_defer` | 기본 [자동 응답 지연](responses.md#deferring) 시간. 언제든 바꿀 수 있습니다 |

`bot.rest`, `bot.http`, `bot.gateway`는 `start()` 전에 접근하면 `StateError`를 일으킵니다.

## 로깅 { #logging }

bostie는 표준 `logging` 모듈에 다음 이름으로 로그를 남깁니다.

| 로거 | 기록하는 내용 |
|---|---|
| `bostie.bot` | 실패했거나 응답하지 않은 핸들러, 리스너에 필요한 인텐트, 처리되지 않은 인터랙션 |
| `bostie.gateway` | 연결, 연결 끊김, 재개, 백프레셔, 잘못된 요청 |
| `bostie.rest` | Rust HTTP 클라이언트의 레이트 리밋과 재시도 |
| `bostie.sync` | 명령어 동기화마다 바뀐 내용 |
| `bostie.context` | 적용할 수 없었던 응답. 예: 수정에 붙은 에페메랄 플래그 |
| `bostie.events` | 해당 이벤트에 맞지 않은 페이로드. 이벤트 이름마다 한 번씩 |
| `bostie.voice` | 음성 연결, DAVE, 싱크, 리시버, 플레이어 |
| `bostie.bridge` | Python과 Rust 코어 사이의 연결 |

Rust 코어도 같은 로거로 로그를 남기며, 봇이 시작될 때 각 로거의 레벨을 읽습니다. 게이트웨이를 자세히 보려면 `run()` 전에 `logging.getLogger("bostie.gateway").setLevel(logging.DEBUG)`를 설정하세요.

## bostie가 다른 곳을 향하게 하기 { #pointing-bostie-elsewhere }

`Bot(api_url=...)`은 REST 요청을 다른 기본 URL로 보냅니다. 예를 들어 여러 프로세스가 함께 쓰는 레이트 리밋 프록시나, 테스트용 [페이크 Discord](testing.md)가 있습니다. 지정하지 않으면 bostie는 봇이 시작될 때 `BOSTIE_API_URL`을 읽고, 이것도 설정되지 않았으면 `https://discord.com/api/v10`을 사용합니다. 레이트 리밋 프록시 뒤에서는 `http_settings=HTTPSettings(rate_limiter=False)`를 넘겨 대기를 프록시에 맡기세요. 나머지 HTTP 설정은 [REST 가이드](rest.md#rate-limits-retries-and-timeouts)에서 다룹니다.

## 한 프로세스에서 여러 봇 실행하기 { #several-bots-in-one-process }

각 `Bot`은 Rust 코어와의 연결, 캐시, HTTP 클라이언트를 따로 가지므로, 한 프로세스가 하나의 이벤트 루프에서 여러 봇을 실행하고 각각 따로 시작하고 닫을 수 있습니다.

## 상태 보관하기 { #keeping-state }

봇은 평범한 객체입니다. `Bot`을 상속해 속성을 추가하면 됩니다. [수명 주기 예제](../examples.md#lifecycle)는 `Starting` 리스너에서 연 데이터베이스 연결을 `Stopped` 리스너에서 닫는 방식으로 이렇게 합니다. 모든 `Bot` 옵션은 [Bot 옵션 레퍼런스](../reference/bot-options.md)에 정리되어 있습니다.
