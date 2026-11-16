---
source: 63c601a21b6ce6311976189053f39ff84cf42f5d
description: "권한, 역할, 소유자, 쿨다운, 동시 실행 체크로 명령어를 제한하고, 핸들러 전후에 훅을 실행하며, 에러 훅으로 에러를 처리합니다."
---

# 체크, 훅, 에러

![팔짱을 끼고 씩씩거리는 Bostie](../assets/mascot/bostie-angry.webp){ align=right width="180" loading=lazy }

체크는 핸들러를 실행해도 되는지 결정합니다. 훅은 핸들러 앞뒤로 코드를 실행합니다. 에러 훅은 예외가 발생했을 때 응답합니다.

## 체크 { #checks }

```python
--8<-- "docs/snippets/checks/command_checks.py"
```

체크는 컨텍스트를 받아 `bool`을 반환하거나, `await`해서 `bool`을 얻는 함수입니다. `False`를 반환하면 `CheckFailure`로 실행을 거부하며, 체크가 직접 `CheckFailure`를 발생시킬 수도 있습니다. 이때 예외의 메시지가 사용자에게 전달됩니다. 체크는 명령어, 그룹, 하위 명령어, 사용자 명령어나 메시지 명령어, 진입점 명령어, 컴포넌트 라우트와 모달 라우트의 `checks=[...]` 설정에 넣습니다. `@router.check`는 라우터의 모든 핸들러에 적용할 체크를 등록하며, 봇도 라우터입니다.

봇의 체크가 가장 먼저 실행되고, 이어서 가장 바깥쪽 라우터부터 안쪽으로 각 라우터의 체크, 위쪽 그룹부터 아래로 각 그룹의 체크, 마지막으로 명령어 자체의 체크가 실행됩니다. 처음으로 거부하는 체크에서 실행이 멈춥니다. 자동 완성에서는 체크를 실행하지 않습니다.

`bostie.checks`에는 자주 쓰는 체크가 들어 있습니다.

| 체크 | 거부할 때 발생하는 예외 | 거부하는 경우 |
|---|---|---|
| `has_permissions(permissions)` | `MissingPermissions` | 사용자가 채널에서 `permissions` 중 하나라도 없거나, 개인 메시지(DM)에 있을 때 |
| `bot_has_permissions(permissions)` | `BotMissingPermissions` | 봇이 채널에서 그중 하나라도 없을 때 |
| `Cooldown(rate, seconds, per="user")` | `CooldownActive` | 해당 키가 최근 `seconds`초 동안 이미 `rate`번 사용했을 때 |
| `MaxConcurrency(number, per="global")` | `MaxConcurrencyReached` | 해당 키의 실행이 `number`개 진행 중일 때 |
| `has_role(role)` | `MissingRole` | 사용자에게 ID나 이름으로 지정한 역할이 없거나, 개인 메시지(DM)에 있을 때 |
| `has_any_role(*roles)` | `MissingRole` | 사용자에게 역할이 하나도 없을 때 |
| `is_owner` | `NotOwner` | 사용자가 `bot.owner_ids`에 없을 때 |

`MaxConcurrency`처럼 `release(ctx)` 메서드도 가진 체크 객체, 즉 `checks.ReleasingCheck`는 핸들러가 어떻게 끝나든 끝나면 해제됩니다.

`per`는 `"user"`, `"guild"`(개인 메시지에서는 사용자), `"channel"`, `"global"`, 또는 컨텍스트를 받아 해시 가능한 키를 반환하는 함수입니다. 권한 체크는 Discord가 인터랙션과 함께 보내는 권한을 읽으므로 캐시도 요청도 필요하지 않습니다. 권한 체크의 에러에는 부족한 `Permissions`인 `missing`이 들어 있고, `CooldownActive`에는 초 단위의 `retry_after`가 들어 있습니다.

## 역할과 소유자 { #roles-and-owners }

```python
--8<-- "docs/snippets/checks/staff_checks.py"
```

`has_role`과 `has_any_role`은 역할 ID나 이름을 받습니다. ID는 Discord가 인터랙션과 함께 보내는 역할과 비교하므로 캐시가 필요하지 않습니다. 이름은 캐시된 서버의 역할에서 찾으므로, 캐시가 없는 봇이나 [HTTP 전용 앱](http-interactions.md)에서는 역할을 ID로 지정합니다. `MissingRole`에는 요구한 `roles`가 들어 있고, 그 메시지는 ID로 지정한 역할을 멘션하며 Discord는 이를 역할 이름으로 표시합니다.

`is_owner`는 괄호 없이 그대로 쓰는 체크입니다. 통과시키는 사용자는 `bot.owner_ids`에 들어 있습니다. `Bot(...)`에 `owner_ids`를 넘겼다면 그 값이고, 아니면 봇이 시작할 때 애플리케이션에서 읽은 앱 소유자입니다. 팀 소유의 앱이라면 팀 소유자와, 초대를 수락한 관리자 및 개발자입니다. 읽기 전용 멤버는 제외됩니다.

## 사용자에게 보여 주는 메시지 { #what-the-user-is-told }

`CheckFailure`를 처리하는 에러 훅이 없으면, bostie는 그 메시지로 에페메랄 응답을 보냅니다. "You need the Manage Messages permission.", "The bot needs the Manage Messages permission.", "in 42 seconds" 같은 상대 타임스탬프가 붙은 "Try again", "This is already running; try again once it is done.", "You need the Moderator role.", "Only the bot's owners can use this.", 그리고 단순히 `False`를 반환한 경우의 "You cannot use this."입니다.

## 실행 전후 훅 { #hooks-before-and-after }

```python
--8<-- "docs/snippets/checks/error_hooks.py"
```

`@router.before_invoke`는 체크를 통과한 뒤 핸들러보다 먼저 실행되는 `async def hook(ctx)`를 등록하고, `@router.after_invoke`는 핸들러 다음에, 핸들러가 예외를 발생시킨 경우에도 실행되는 훅을 등록합니다. 훅은 라우터의 모든 명령어, 컴포넌트 라우트, 모달 라우트에서 실행되므로, 컴포넌트를 받을 수 있는 훅은 `Context`라고 가정해서는 안 됩니다. 봇의 before 훅이 가장 먼저, after 훅이 가장 나중에 실행됩니다. after 훅에서 발생한 예외는 로그에 남을 뿐 응답을 바꾸지 않습니다.

## 에러 훅 { #error-hooks }

체크, 컨버터, before 훅, 핸들러에서 발생한 예외는 에러 훅으로 갑니다. `@x.on_error`는 모든 `Exception`을 처리하는 `async def hook(ctx, error)`를 등록하고, `@x.on_error(NotFound, ValueError)`는 그 타입과 하위 클래스를 처리하는 훅을 등록합니다. `x`는 명령어, 그룹, 컴포넌트 라우트나 모달 라우트, 라우터, 봇이며, 각각 예외 타입마다 훅을 하나씩 둘 수 있습니다.

bostie는 다음 순서로 훅을 찾고, 처음 찾은 훅이 에러를 처리합니다.

1. 명령어, 그다음 명령어의 그룹과 최상위 그룹
2. 명령어가 속한 라우터, 그다음 그 라우터를 포함하는 각 라우터를 거쳐 봇까지

한 위치 안에서는 에러와 가장 가까운 클래스의 훅이 우선합니다. `NotFound`용 훅이 `HTTPException`용 훅보다 먼저입니다. 같은 에러를 다시 발생시키는 훅은 위 예제의 `slow_down`처럼 이 순서상 다음 훅으로 에러를 넘깁니다. 다른 에러를 발생시키는 훅이라면 그 에러는 로그에 남습니다.

컨텍스트 타입은 `ErrorContext`이며, `Context`, `ComponentContext`, `ModalContext`, `AutocompleteContext` 중 하나입니다. 자동 완성 컨텍스트는 `respond`를 할 수 없으므로 위 예제처럼 확인하세요.

훅이 응답하지 않고 반환하면 bostie는 경고를 로그에 남기고 "Something went wrong."으로 응답합니다. 에러를 처리하는 훅이 없으면, bostie는 `CheckFailure`에는 그 메시지로 응답하고, 그 밖의 에러는 트레이스백과 함께 로그에 남긴 뒤 "Something went wrong."으로 응답합니다. 핸들러가 이미 응답했다면 아무것도 응답하지 않으며, 자동 완성에는 빈 제안 목록을 돌려줍니다.

## 핸들러 밖의 에러 { #errors-outside-handlers }

리스너와 태스크에는 응답할 사용자가 없습니다. 리스너에서 발생한 예외는 `ListenerError` 이벤트로, 태스크에서 발생한 예외는 `TaskError`로 전달되며, 이를 수신하는 리스너가 없으면 bostie가 로그에 남깁니다. [이벤트](events.md#when-a-listener-raises)와 [백그라운드 태스크](tasks.md)를 참고하세요.

## 예외 타입 { #exception-types }

Discord, 네트워크, 봇의 상태와 관련해 bostie가 발생시키는 예외는 모두 `BostieError`입니다. 잘못된 인자에는 대신 `ValueError`나 `TypeError`가 발생합니다. 일부 에러는 표준 예외이기도 합니다. `StateError`는 `RuntimeError`, `MissingIntent`는 `ValueError`, `StartupError`는 `OSError`, `CommandDefinitionError`는 `TypeError`입니다. 명령어와 인터랙션에 관한 에러는 공통 기반 클래스 `CommandError`를, 음성 에러는 `VoiceError`를 공유합니다.

| 예외 | 발생하는 경우 |
|---|---|
| `HTTPException` | REST 요청이 에러 상태로 끝났을 때. `status`, `code`(`JSONErrorCode`), `message`, `errors`, 원시 `body`를 가짐 |
| `BadRequest`, `Forbidden`, `NotFound`, `ServerError` | 400, 403, 404, 5xx |
| `RateLimited` | 429이거나, `max_rate_limit_wait`보다 오래 기다려야 하는 요청을 bostie가 보류했을 때. `retry_after`, `is_global` |
| `InvalidToken` | REST나 게이트웨이에서 Discord가 토큰을 거부했을 때 |
| `TransportError` | Discord가 응답하기 전에 요청이 실패했을 때 |
| `UnexpectedResponse` | Discord가 요청을 받아들였지만 응답을 디코딩할 수 없을 때. `raw`에 응답이 들어 있음 |
| `RequestBudgetExhausted` | 최근 잘못된 요청이 너무 많아, IP를 보호하려고 bostie가 요청을 거부했을 때 |
| `GatewayClosed`, `PrivilegedIntentsRequired`, `ShardingRequired` | Discord가 게이트웨이를 완전히 닫았을 때. 뒤의 두 개는 코드 4014와 4011 |
| `ShardNotRunning`, `GatewayQueueFull` | 이 프로세스가 실행하지 않는 샤드에 게이트웨이 명령을 보내거나, 대기 중인 명령이 너무 많을 때 |
| `StateError` | `start()` 전의 `bot.rest`처럼 객체의 상태상 허용되지 않는 호출 |
| `MissingIntent` | 기능에 필요한 인텐트를 봇이 요청하지 않았을 때 |
| `MemberRequestRateLimited` | Discord가 멤버 요청을 거부했을 때. `retry_after` |
| `StartupError` | bostie가 필요한 시스템 자원을 준비하지 못했을 때 |
| `CommandDefinitionError` | 명령어, 그룹, 옵션이 Discord의 규칙을 어길 때. 데코레이터가 발생시킴 |
| `CommandSyncError` | 동기화 중에 Discord가 명령어를 거부했을 때 |
| `OptionConversionError` | 옵션이나 대상이 타입 힌트로 변환되지 않을 때 |
| `InteractionResponded`, `InteractionNotAnswered`, `InteractionExpired` | 인터랙션의 상태상 허용되지 않는 응답 호출 |
| `CheckFailure`와 그 하위 클래스 | 체크가 실행을 거부했을 때 |

음성 에러는 [음성 페이지](../voice/index.md#errors)에 있습니다. [에러 레퍼런스](../reference/api/errors.md)에는 모든 클래스와 속성이 나열되어 있습니다.
