---
source: 77fd407beead25f005f3979ee300e3dbfac29482
description: "Discord 봇을 한 프로세스나 여러 프로세스에서 샤딩하고, 프로세스 간 identify를 조율하며, 샤드 이벤트를 받고, 재시작 뒤 세션을 재개합니다."
---

# 샤딩과 재개

![나란히 달리는 세 마리의 Bostie](../assets/mascot/bostie-running.webp){ align=right width="220" loading=lazy }

Discord는 봇이 속한 서버를 샤드라고 부르는 여러 게이트웨이 연결로 나눕니다. 서버 `g`는 샤드 `(g >> 22) % shard_count`에 속합니다. 2500개가 넘는 서버에 있는 봇은 반드시 샤딩해야 합니다. 샤딩하지 않은 연결은 Discord가 코드 4011로 닫고, `start()`가 `ShardingRequired`를 일으킵니다.

## 한 프로세스의 샤드 { #shards-in-one-process }

기본값인 `Bot(shard_count="auto")`는 사용할 샤드 수를 Discord에 물어보고 모든 샤드를 실행합니다. 따로 할 일은 없습니다. bostie는 Discord의 identify 제한 안에서 샤드를 시작하며, 모든 샤드가 같은 리스너, 캐시, 핸들러로 이벤트를 보냅니다. `event.shard_id`는 이벤트가 어느 샤드에서 왔는지 알려 줍니다.

## 여러 프로세스에 나눈 샤드 { #shards-across-processes }

```python
--8<-- "docs/snippets/sharding/cluster.py"
```

`shard_count=16`은 전체 샤드 수를 고정하고, `shard_ids`는 이 프로세스가 실행할 샤드를 고릅니다. `shard_ids`에는 명시적인 `shard_count`가 필요하며, ID는 서로 달라야 하고 `range(shard_count)` 안에 있어야 합니다.

Discord는 봇마다 동시에 할 수 있는 identify 수를 제한합니다. bostie는 한 프로세스 안에서는 이 제한을 지키고, 여러 프로세스 사이에서는 `before_identify`로 조율합니다. bostie는 각 identify 전에 `before_identify(shard_id)`를 await하므로, 이 훅에서 프로세스들이 함께 쓰는 락의 차례를 기다릴 수 있습니다. 예제의 `identify_lock`은 이런 공유 락을 대신하며, 한 버킷의 identify 사이에 Discord가 요구하는 5초 동안 락을 잡고 있습니다. 훅이 예외를 일으키면 bostie는 에러를 로그에 남기고 그대로 identify합니다.

## 샤드 이벤트 { #shard-events }

| 이벤트 | 필드 | 전송 시점 |
|---|---|---|
| `ShardReady` | `shard_id` | 샤드의 새 세션이 준비되고 서버가 모두 도착했을 때 |
| `ShardDisconnect` | `shard_id`, `code`, `reason`, `resuming` | 연결이 끊겼을 때. `resuming`이면 샤드가 세션을 재개하고 놓친 이벤트를 다시 받으며, 아니면 새 세션을 시작합니다 |
| `ShardStop` | `shard_id`, `code`, `reason` | `close()`가 실행되었거나 Discord가 끝내서 샤드가 완전히 멈췄을 때 |
| `ShardBackpressure` | `shard_id`, `queued_bytes` | 이벤트 루프가 뒤처져 샤드가 읽기를 잠시 멈췄을 때 |

재연결과 재개는 직접 처리할 필요가 없습니다. `bot.latency`는 샤드들의 평균 하트비트 왕복 시간이고, `bot.gateway.shard(shard_id).latency`는 샤드 하나의 왕복 시간입니다.

## 재시작 뒤 재개하기 { #resuming-after-a-restart }

재시작하면 보통 모든 샤드에서 새 세션이 시작되고, 그 사이 몇 분 동안의 이벤트는 사라집니다. `Bot(session_file="sessions.json")`을 지정하면 `close()`가 세션을 열어 둔 채 파일에 기록합니다. 다음 `start()`는 파일을 읽고 삭제한 뒤, 현재 `shard_count`와 샤드 ID에 맞는 저장된 세션을 각각 재개하므로, 봇이 내려가 있던 동안 일어난 이벤트를 Discord가 다시 보내 줍니다. 읽을 수 없는 파일은 로그에 남기고 무시하며, 저장된 세션이 없는 샤드는 identify합니다.

`bot.run()`은 Ctrl+C와 SIGTERM에서 봇을 닫으므로, 프로세스 관리자나 배포로 재시작할 때는 세션이 저장됩니다. 크래시에서는 저장되지 않으며, 다음 시작에서 identify합니다. 생성된 지 5초가 넘어서 다시 전달된 인터랙션은 토큰으로 제때 응답할 수 없으므로 경고와 함께 건너뜁니다.
