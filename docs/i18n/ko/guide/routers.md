---
source: 54b3a6f7e7f026cf8621c75603332297ea4c5a00
---

# 라우터와 모듈

`Router`는 봇처럼 명령어, 리스너, 라우트, 태스크, 체크, 훅, 에러 훅을 담지만 봇 자체는 아닙니다. 라우터로 봇을 모듈로 나누고, 명령어 묶음에 공통 설정을 주고, 봇이 실행되는 동안 교체할 수 있습니다. 봇 자체도 라우터이므로, 이 페이지의 모든 내용은 봇에서도 그대로 동작합니다.

```python
--8<-- "docs/snippets/routers/moderation_router.py"
```

라우터에는 봇의 모든 데코레이터가 있습니다. `command`, `group`, `user_command`, `message_command`, `entry_point`, `listen`, `listen_raw`, `on_component`, `on_modal`, `every`, `check`, `before_invoke`, `after_invoke`, `on_error`입니다. `bot.include(router)`는 라우터의 모든 것을 봇에 추가하며, 나중에 라우터에 등록되는 것도 함께 추가합니다. 라우터의 핸들러 안에서 `router.bot`은 라우터가 포함된 봇입니다. 포함되기 전에는 `StateError`를 일으킵니다.

라우터는 중첩할 수 있습니다. `router.include(other)`도 같은 방식으로 동작하며, 바깥 라우터를 봇에 포함하면 둘 다 함께 들어옵니다.

## 기본값 { #defaults }

`Router(...)`는 자신에게 등록되는 명령어와 라우트의 기본값을 키워드 인자로 받습니다.

| 기본값 | 적용 대상 |
|---|---|
| `ephemeral` | 명령어와 라우트의 응답 |
| `auto_defer` | 명령어와 라우트 |
| `default_permissions` | 최상위 명령어 |
| `nsfw` | 최상위 명령어 |
| `contexts`, `integration_types` | 전역 명령어 |

명령어나 라우트는 먼저 자기 설정을, 그다음 자기 라우터의 기본값을, 그다음 자신을 포함하는 각 라우터의 기본값을 가까운 것부터 바깥쪽 순서로, 마지막으로 봇의 기본값을 사용합니다. 예제에서 `/ban`은 멤버 차단하기(Ban Members) 권한이 없는 멤버에게는 보이지 않고, 서버에서만 쓸 수 있으며, 에페메랄로 응답하므로 명령어를 실행한 관리자만 버튼을 보고 누를 수 있습니다.

라우터의 체크, before 훅, after 훅은 그 라우터의 핸들러에 적용됩니다. 그 라우터를 포함하는 라우터의 것도 가장 바깥쪽부터 실행됩니다. 에러 훅은 [체크, 훅, 에러](checks.md#error-hooks)에서 설명하듯 가장 가까운 라우터부터 바깥쪽으로 찾습니다.

## 모듈 { #modules }

`bot.include("greetings")`는 `greetings` 모듈을 import하고, 그 모듈이 `router`라는 이름으로 가진 `Router`를 포함합니다. 그런 라우터가 없는 모듈은 `TypeError`를 일으킵니다.

`bot.exclude("greetings")`는 라우터가 추가한 모든 것을 제거하고, 태스크를 취소하며, Python이 그 모듈과 하위 모듈을 잊게 하므로, 다시 포함하면 파일을 새로 import합니다. `bot.reload("greetings")`는 두 가지를 한 번에 합니다. 새 import나 포함이 실패하면 이전 라우터가 그대로 포함된 채 남고 에러가 발생합니다. 다시 불러오면 핸들러는 바로 바뀝니다. 명령어 정의가 바뀌었다면 그 뒤에 `await bot.sync_commands()`를 호출하세요. [모듈식 봇 예제](../examples.md#modular-bot)의 `/reload` 명령어가 이렇게 합니다.

`exclude`와 `reload`는 `Router` 객체도 받으므로, 모듈의 `router`가 아닌 라우터에도 쓸 수 있습니다.

## 충돌 { #conflicts }

이름이 같은 명령어 두 개, 이름이 같은 사용자 명령어나 메시지 명령어 두 개, 템플릿이 같은 라우트 두 개, 또는 태스크로 두 번 등록한 함수 하나는 두 번째 것이 추가될 때 예외를 일으킵니다. 라우터가 가져온 경우도 마찬가지입니다. 라우터는 한 번만 포함할 수 있으며, 자기 자신에게는 포함할 수 없습니다.

## 내부 들여다보기 { #looking-inside }

`router.commands`, `router.context_menus`, `router.component_routes`, `router.modal_routes`, `router.tasks`, `router.listeners`에는 등록된 것이 들어 있습니다. 명령어는 이름과 서버별로(전역 명령어라면 `("rules", ())`처럼), 사용자 명령어와 메시지 명령어는 타입, 이름, 서버별로, 라우트는 템플릿별로, 태스크는 함수별로, 리스너는 이벤트별로 담깁니다. `router.walk_commands()`는 핸들러가 있는 모든 슬래시 명령어와 하위 명령어를 내놓습니다. `router.match_component(custom_id)`와 `router.match_modal(custom_id)`는 커스텀 ID가 도달하는 라우트와 그 자리 표시자 값을 반환하거나 `None`을 반환하며, `router.find_error_handlers(error)`는 예외를 처리할 에러 훅을 가장 가까운 것부터 내놓습니다.
