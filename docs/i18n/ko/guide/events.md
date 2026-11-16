---
source: 0402229d2b242ba10aed929b9dedd0087f57b406
description: "타입이 지정된 리스너로 Discord 게이트웨이 이벤트를 듣고 기다리거나 스트림으로 받습니다. 원시 이벤트, 직접 만든 이벤트, 필요한 인텐트를 다룹니다."
---

# 이벤트

![울리는 종에게 인사하는 Bostie](../assets/mascot/bostie-events.webp){ align=right width="180" loading=lazy }

Discord는 게이트웨이 이벤트로 봇에게 무슨 일이 일어났는지 알려 줍니다. 메시지가 전송되었다거나, 멤버가 들어왔다거나, 역할이 바뀌었다는 식입니다. 리스너는 이벤트 하나를 받는 비동기 함수입니다.

```python
--8<-- "docs/snippets/events/listeners.py"
```

`@bot.listen`은 첫 번째 매개변수의 타입 힌트를 읽고, 그 이벤트 클래스에, 또는 유니언이면 유니언의 각 클래스에 함수를 등록합니다. 힌트가 없거나 더 넓은 힌트를 가진 함수라면 `@bot.listen(MessageUpdate)`처럼 클래스를 직접 지정합니다. 이벤트 클래스는 `bostie.events`에 있으며, [이벤트 목록](../reference/event-list.md)에 모든 클래스가 인텐트, 필드와 함께 정리되어 있습니다. [라우터](routers.md)에도 같은 데코레이터가 있습니다.

각 리스너는 자기만의 태스크로 실행되므로, 느린 리스너가 다른 리스너나 다음 이벤트를 붙잡아 두지 않습니다. 태스크는 바로 시작됩니다. 리스너 코드 중 첫 번째 `await`까지는 다음 이벤트를 처리하기 전에 실행됩니다. 예외는 `Starting`과 `Stopped`의 리스너로, `start()`와 `close()`가 이들을 하나씩 차례로 await합니다. `bot.remove_listener(function)`은 함수가 듣고 있는 모든 이벤트에서 그 함수의 등록을 해제합니다.

## 듣는 것만 받기 { #only-what-you-listen-to }

bostie는 봇 안의 무언가가 사용하는 디스패치 이름만 전달하도록 Rust 코어에 요청합니다. 여기에는 리스너, 캐시, 명령어와 컴포넌트 라우팅, 대기 중인 wait가 포함됩니다. 나머지 이벤트는 Python에 닿기 전에 버려지므로, 아무것도 듣지 않으면 비용도 들지 않습니다. 타입이 지정된 이벤트는 디스패치마다 한 번만, 그리고 필요한 곳이 있을 때만 디코딩됩니다.

## 인텐트 { #intents }

Discord는 대부분의 이벤트를 해당 인텐트를 요청한 봇에게만 보냅니다. `Bot(intents=...)`의 기본값은 `Intents.DEFAULT`로, 특권 인텐트 세 개를 뺀 모든 인텐트입니다. 봇의 인텐트로는 받을 수 없는 이벤트에 리스너를 등록하면, bostie는 추가해야 할 인텐트를 알려 주는 경고를 로그에 남기고, 특권 인텐트라면 Developer Portal에서도 켜야 한다는 점을 함께 알려 줍니다. 자세한 내용은 [인텐트](gateway.md#intents)에서 다룹니다.

## 이전 값과 캐시 { #old-values-and-the-cache }

업데이트 이벤트는 변경 전 [캐시](cache.md)에 있던 객체인 `old`를 담고, 삭제 이벤트는 삭제된 객체를 담습니다. `RoleUpdate.old`, `ScheduledEventUpdate.old`, `MessageDelete.message`, `MemberRemove.member`가 그 예입니다. 캐시에 객체가 없었으면 이 값은 `None`입니다. 메시지는 `Bot(max_messages=...)`를 지정했을 때만, 멤버는 `members=True`일 때만, 프레즌스는 `presences=True`일 때만 캐시됩니다.

## 이벤트 기다리기 { #waiting-for-an-event }

```python
--8<-- "docs/snippets/events/waiting.py"
```

`await bot.wait_for(Event, check=None)`은 `check`가 참을 반환하는, 해당 클래스의 다음 이벤트를 반환합니다. `async with bot.stream(Event, check=None) as events:`는 블록이 실행되는 동안 조건에 맞는 모든 이벤트를 큐에 쌓아 `async for`로 받을 수 있게 하며, 루프 본문이 실행되는 동안 도착한 이벤트도 잃어버리지 않습니다. 둘 다 자체 타임아웃이 없으므로 `asyncio.timeout()`으로 제한하세요. `check`에서 발생한 예외는 wait에서 그대로 발생합니다. `bot.wait_for_component`와 `bot.wait_for_modal`은 [컴포넌트 사용](components.md#waiting-for-a-click-inside-a-handler)과 [모달 제출](modals.md#waiting-for-the-submit-inside-a-handler)에 대해 같은 일을 합니다.

## 원시 이벤트와 bostie가 모델링하지 않는 이벤트 { #raw-events-and-events-bostie-does-not-model }

```python
--8<-- "docs/snippets/events/custom_events.py"
```

`@bot.listen_raw("NAME", ...)`은 지정한 디스패치 이름마다, 모델링된 것이든 아니든 `RawEvent`를 전달하며, `RawEvent` 힌트를 가진 리스너는 모든 디스패치를 받습니다. `RawEvent`에는 디스패치 이름 `name`, 시퀀스 번호 `seq`, 그리고 페이로드가 `raw`(매번 새 `dict`)나 `raw_json`(바이트)으로 들어 있습니다. 타입이 지정된 모든 이벤트에도 `raw`와 `raw_json`이 있습니다.

Discord가 보낸 페이로드가 해당 클래스에 맞지 않으면, 예를 들어 문서에서 필수라고 한 필드가 없으면, bostie는 이벤트 이름마다 한 번 경고를 로그에 남깁니다. 타입이 지정된 리스너는 그 이벤트를 건너뛰지만, `RawEvent` 리스너는 여전히 받습니다.

## 직접 정의한 이벤트 { #events-of-your-own }

`kw_only=True`로 `BotEvent`를 상속하고, `bot.dispatch(event)`로 인스턴스를 보냅니다. 그러면 리스너, wait, 스트림이 다른 이벤트와 똑같이 그 이벤트를 받습니다. `NAME`이 없는 하위 클래스는 모듈과 클래스 이름을 따라 이름이 정해집니다. 이벤트 클래스는 frozen `msgspec` 구조체이므로, 필드는 어노테이션으로 선언합니다.

## 수명 주기 이벤트와 샤드 이벤트 { #lifecycle-and-shard-events }

bostie는 봇에 관한 자체 이벤트를 보냅니다. [수명 주기](running.md#the-lifecycle) 전후에는 `Starting`, `Started`, `Stopping`, `Stopped`를, 각 [샤드](sharding.md)에 대해서는 `ShardReady`, `ShardDisconnect`, `ShardStop`, `ShardBackpressure`를, 실패에 대해서는 `ListenerError`와 `TaskError`를 보냅니다. 음성 연결은 같은 `@bot.listen`으로 [음성 이벤트](../voice/index.md#events)를 보냅니다.

## 리스너가 예외를 일으킬 때 { #when-a-listener-raises }

리스너에서 발생한 예외는 `listener`, `event`, `error`를 담은 `ListenerError` 이벤트로 전송됩니다. `ListenerError` 리스너가 없으면 bostie가 트레이스백과 함께 예외를 로그에 남기고, 리스너가 있으면 로깅은 직접 처리해야 합니다. `ListenerError` 리스너에서 발생한 예외는 로그에만 남습니다.
