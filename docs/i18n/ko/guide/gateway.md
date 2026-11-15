---
source: 3337dc9b9c1508c500eac3639bbfb9fc948f4a27
---

# 인텐트, 프레즌스, 게이트웨이

게이트웨이는 Discord가 이벤트를 보내고 봇이 프레즌스와 요청을 보내는 WebSocket입니다. bostie의 Rust 코어가 하트비트, 재개, 재연결, 압축, 전송 레이트 리밋을 처리하며 연결을 유지합니다. 이 페이지에서는 봇이 게이트웨이에 요청하는 내용을 다룹니다.

## 인텐트 { #intents }

Discord는 이벤트의 인텐트를 요청한 봇에게만 그 이벤트를 보냅니다. `Bot(intents=...)`는 `Intents` 플래그를 받으며, 생략하면 `Intents.DEFAULT`, 즉 특권 인텐트 세 개를 뺀 모든 인텐트를 사용합니다.

| 특권 인텐트 | 필요한 경우 |
|---|---|
| `GUILD_MEMBERS` | 멤버 참가, 업데이트, 제거 이벤트, 캐시의 모든 멤버, 모든 멤버 요청 |
| `GUILD_PRESENCES` | 프레즌스 업데이트와 캐시의 프레즌스 |
| `MESSAGE_CONTENT` | 서버 메시지의 내용, 임베드, 첨부 파일, 컴포넌트. 봇을 멘션한 메시지는 예외이며, 개인 메시지(DM)에는 인텐트가 필요 없습니다 |

특권 인텐트는 Developer Portal의 **Bot** 페이지에서도 켜야 합니다. 켜지 않으면 Discord가 코드 4014로 연결을 닫고, `start()`가 `PrivilegedIntentsRequired`를 일으킵니다. 이 예외의 메시지에 어디서 켜야 하는지가 나와 있습니다. 인텐트는 `|`로 추가합니다(`Intents.DEFAULT | Intents.MESSAGE_CONTENT`). 리스너에 필요한 인텐트가 봇에 없으면 bostie가 경고하고, 옵션이나 요청에 필요한 인텐트가 없으면 `MissingIntent`를 일으킵니다.

음성에는 `GUILD_VOICE_STATES`가 필요하며, `DEFAULT`에 포함되어 있습니다.

```python
--8<-- "docs/snippets/gateway/intent_presets.py"
```

개별 인텐트 외에 프리셋이 세 개 있습니다. `Intents.UNPRIVILEGED`라고도 하는 `Intents.DEFAULT`는 특권 인텐트를 뺀 모든 인텐트를, `Intents.PRIVILEGED`는 특권 인텐트 세 개를, `Intents.ALL`은 둘 다를 담습니다. 위 예처럼 `& ~`로 프리셋에서 인텐트를 뺄 수 있습니다.

## 프레즌스 { #presence }

```python
--8<-- "docs/snippets/gateway/presence_and_requests.py"
```

`Bot(status=..., activities=[...], afk=False, idle_since=None)`는 봇이 연결될 때 표시할 내용을 정합니다. `await bot.update_presence(...)`는 모든 샤드와 나중에 연결되는 샤드에서 이를 바꾸며, 생략한 옵션은 기존 값을 유지합니다.

`status`는 `Status`로, `ONLINE`, `IDLE`, `DND`, `INVISIBLE` 중 하나입니다. 활동은 `bostie.activity`로 만듭니다.

| 빌더 | 표시 |
|---|---|
| `activity.playing(name)` | *name* 하는 중(Playing *name*) |
| `activity.listening(name)` | *name* 듣는 중(Listening to *name*) |
| `activity.watching(name)` | *name* 시청 중(Watching *name*) |
| `activity.competing(name)` | *name* 참가 중(Competing in *name*) |
| `activity.streaming(name, url)` | *name* 방송 중(Streaming *name*). Discord가 지원하는 스트리밍 서비스용 |
| `activity.custom(text)` | *text*를 담은 사용자 지정 상태 |

`custom`을 뺀 나머지는 두 번째 줄 텍스트인 `state=`를 받으며, `activity.ActivityDict`를 반환합니다.

## 요청 { #requests }

일부 데이터는 게이트웨이를 통해, 요청에 대한 응답으로만 받을 수 있습니다.

- `await bot.request_members(guild_id, query="ab", limit=10)`은 사용자명이나 별명이 `query`로 시작하는 멤버를 최대 `limit`명(0~100)까지 반환합니다. 대신 `user_ids=[...]`로 ID를 지정해 멤버를 최대 100명까지 요청할 수도 있습니다. 둘 다 없으면 모든 멤버를 요청하며, 이때는 `GUILD_MEMBERS`가 필요합니다. `presences=True`는 멤버의 프레즌스도 함께 가져오며 `GUILD_PRESENCES`가 필요합니다. 응답은 `members`, `presences`, 그리고 Discord가 찾지 못한 ID인 `not_found`를 담은 `RequestedMembers`입니다. Discord는 이 요청에 레이트 리밋을 걸며, 거부된 요청은 `retry_after`를 담은 `MemberRequestRateLimited`를 일으킵니다.
- `await bot.request_soundboard_sounds(guild_id)`는 서버의 사운드보드 사운드를 반환합니다.
- `await bot.request_channel_info(guild_id)`는 서버 채널의 음성 상태와 음성 시작 시각을 반환합니다. `fields=["status"]`로 더 적게 요청할 수 있습니다.

각 요청은 응답의 각 부분을 최대 30초(멤버는 `timeout=`) 동안 기다리고, 그 뒤에는 `TimeoutError`를 일으킵니다. 새 세션을 시작하고 있는 샤드에 보낸 요청은 `StateError`를 일으키므로, 샤드가 준비된 뒤 다시 시도하세요.

## 원시 게이트웨이 명령 { #raw-gateway-commands }

`bot.gateway.send(op, d)`는 어떤 게이트웨이 명령이든 보냅니다. opcode 3(프레즌스)은 모든 샤드로 갑니다. `guild_id`가 있는 페이로드는 그 서버의 샤드로, `guild_ids`가 있는 페이로드는 각 샤드에 해당하는 ID만 나눠서, 나머지는 샤드 0으로 갑니다. Discord의 응답은 이벤트로 도착하며, 무언가가 듣고 있는 이벤트 이름에 대해서만 전달되므로, 원시 명령은 `listen_raw` 리스너나 `wait_for`와 함께 쓰세요.

`bot.gateway`는 다음도 제공합니다. 이 프로세스가 실행하는 샤드인 `shard_ids`, Discord가 서버의 이벤트를 보내는 샤드를 알려 주는 `shard_for(guild_id)`, 자체 `send`, `reconnect`, `latency`를 가진 `shard(shard_id)`, 모든 연결을 끊고 새 연결에서 세션을 재개하는 `reconnect()`, 평균 하트비트 왕복 시간인 `latency`입니다. 이 프로세스가 실행하지 않는 샤드에 보낸 명령은 `ShardNotRunning`을, 너무 많은 명령 뒤에 대기열에 쌓인 명령은 `GatewayQueueFull`을 일으킵니다.

## 백프레셔 { #backpressure }

Rust 코어는 Discord가 보내는 속도대로 이벤트를 읽어 Python에 넘깁니다. 이벤트 루프가 뒤처지면 샤드는 Python이 따라잡을 때까지 읽기를 멈추고, 경고를 로그에 남기며, 대기 중인 바이트 수를 담은 `ShardBackpressure`를 보냅니다. 핸들러나 리스너 안에서 오래 블로킹하는 작업이 원인이므로, 그런 작업은 `asyncio.to_thread`로 스레드에 옮기세요.
