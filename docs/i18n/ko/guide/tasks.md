---
source: b58df394d71cd32e4c45cccfdd0da40ddcb957c3
description: "@bot.every로 몇 초마다 또는 하루 중 정한 시각에 백그라운드 태스크를 실행하고, 봇이 실행되는 동안 시작, 중지, 재시작하거나 일정을 바꿉니다."
---

# 백그라운드 태스크

![알람 시계가 울리는 동안 잠들어 있는 Bostie](../assets/mascot/bostie-sleeping.webp){ align=right width="180" loading=lazy }

`@bot.every`는 인자가 없는 비동기 함수를 봇이 준비된 동안 일정에 따라 실행합니다.

```python
--8<-- "docs/snippets/tasks/scheduled.py"
```

`every(seconds=300)`은 봇이 준비되자마자 함수를 실행하고, 그 뒤로 300초마다 실행합니다. 일정은 밀리지 않습니다. 각 실행은 첫 실행으로부터 `seconds`의 다음 배수 시점에 시작하며, `seconds`보다 오래 걸린 실행은 겹친 틱을 건너뜁니다.

`every(time=...)`은 매일 정해진 한 시각, 또는 여러 시각마다 함수를 실행합니다. `tzinfo`가 없는 `datetime.time`은 UTC로 해석합니다. 위 예처럼 고정 오프셋을 주면 그 오프셋을 계속 따르고, `zoneinfo.ZoneInfo`를 주면 그 시간대의 일광 절약 시간 변경을 따릅니다. `seconds`와 `time` 중 하나만 넘기세요.

## 태스크 제어하기 { #controlling-a-task }

```python
--8<-- "docs/snippets/tasks/task_control.py"
```

`@bot.every`는 `Task`를 반환하며, `check_feeds()`를 await하면 여전히 함수를 한 번 실행합니다. `Task`의 메서드로 봇이 실행되는 동안 일정을 바꿀 수 있습니다.

| `Task` | 동작 |
|---|---|
| `stop()` | 실행 중이든 실행 사이든 태스크를 취소하고, `start()`를 호출할 때까지 봇과 함께 시작하지 않게 합니다. 봇이 실행되기 전에 호출하면 태스크는 `start()`를 기다립니다 |
| `start()` | 봇이 준비된 동안 태스크를 실행합니다. 이미 준비되었다면 지금부터 실행합니다. 실행 중인 태스크는 그대로 계속됩니다 |
| `restart()` | 태스크를 취소하고 새로 시작하므로, 간격 태스크는 바로 실행됩니다 |
| `change_interval(seconds=...)` 또는 `change_interval(time=...)` | 새 일정으로 바꿉니다. 대기는 변경 시점부터, 또는 진행 중인 실행이 끝난 시점부터 새로 시작합니다 |
| `count` | 태스크가 마지막으로 시작된 뒤 끝난 실행 횟수 |
| `seconds`, `times` | 태스크가 따르는 일정 |
| `@task.before`, `@task.after` | `async def hook()`을 등록합니다. 태스크가 시작될 때마다, 그리고 `stop()`, `restart()`, 라우터 제외, `close()` 중 어떤 이유로든 멈출 때마다 await됩니다 |

훅에서 발생한 예외는 실행 중 발생한 예외처럼 `TaskError`로 전송됩니다. `before` 훅이 예외를 일으키면 태스크는 `restart()`할 때까지 멈춘 상태로 남습니다.

## 태스크가 실행되는 시점 { #when-tasks-run }

태스크는 봇이 `Started`를 보낼 때 시작하고, `close()`는 HTTP 클라이언트를 닫기 전에 태스크를 취소합니다. 나중에 포함되는 라우터에 등록된 태스크는 라우터가 포함되자마자 시작하고, 라우터가 제외되면 취소됩니다. [`ASGIApp`](http-interactions.md)만으로 제공되는 앱은 앱이 시작될 때 태스크를 시작합니다.

함수 하나는 태스크로 한 번만 등록할 수 있습니다. 같은 코드를 두 일정으로 실행하려면 함수를 두 개 등록하세요.

## 태스크가 예외를 일으킬 때 { #when-a-task-raises }

실행 중 발생한 예외는 `task`(예외를 일으킨 `Task`)와 `error`를 담은 `TaskError` 이벤트로 전송되며, 태스크는 다음 예정 시각에 다시 실행됩니다. `TaskError` 리스너가 없으면 bostie가 트레이스백과 함께 예외를 로그에 남깁니다.
