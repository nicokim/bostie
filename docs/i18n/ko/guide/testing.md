---
source: 0d5db0165c75f4671bf7c09cfef1394b9bfa9188
description: "Discord 없이 봇을 테스트합니다. 로컬 페이크 게이트웨이, REST API, 음성 서버에서 봇을 실행하고 인터랙션과 이벤트를 보내 결과를 확인합니다."
---

# 봇 테스트하기

![체크 표시가 나타날 때까지 돋보기로 살피는 Bostie](../assets/mascot/bostie-detective.webp){ align=right width="180" loading=lazy }

`bostie.testing`은 localhost에서 페이크 Discord를 실행합니다. 게이트웨이, 봇이 가장 많이 쓰는 라우트를 갖춘 REST API, 그리고 선택적으로 음성 서버를 제공합니다. 실제 봇이 여기에 연결하고, 테스트는 Discord 쪽 역할을 맡아 이벤트와 인터랙션을 보낸 뒤 봇이 돌려보낸 것을 확인합니다. `testing` extra가 필요하며([설치](../install.md)), bostie 자체의 테스트 스위트도 이 위에서 실행됩니다.

```python
--8<-- "docs/snippets/testing/test_greeter.py"
```

`run(scenario)`는 `FakeDiscord`를 시작하고, 그 위에서 `async def scenario(fake)`를 10초 안에 실행한 다음 종료합니다. 일반 함수이므로 pytest에 플러그인이 필요하지 않습니다. `async with running(bot, fake):`는 페이크 위에서 봇을 시작하고, 준비될 때까지 기다린 뒤, 블록이 끝나면 봇을 닫습니다. `Bot(api_url=fake.api_url)`로 봇이 페이크를 바라보게 하거나, 봇을 다른 곳에서 만든다면 `BOSTIE_API_URL`을 설정합니다.

## Discord 쪽 조작하기 { #driving-discords-side }

| 호출 | 동작 |
|---|---|
| `await fake.interaction(type, data)` | `INTERACTION_CREATE`를 보내고, 봇의 첫 응답을 `Recorded` 요청으로 반환합니다 |
| `await fake.dispatch(name, d, shard=0)` | 임의의 게이트웨이 이벤트를 보내고 그 시퀀스 번호를 반환합니다 |
| `fake.expect_callback(payload)` | 직접 디스패치하는 인터랙션에 대해, 첫 응답을 받을 future를 반환합니다 |
| `await fake.send_op(op, d)` | 7(reconnect)이나 9(invalid session) 같은 디스패치가 아닌 opcode를 보냅니다 |
| `await fake.close_gateway(code)`, `fake.drop_gateway()` | 종료 코드와 함께 연결을 닫거나, 종료 코드 없이 연결을 끊습니다 |
| `fake.hold_acks()` | 끊긴 연결처럼 하트비트에 응답하지 않습니다 |
| `await fake.next_identify()`, `await fake.next_resume()` | 봇의 다음 IDENTIFY나 RESUME을 기다립니다 |

인터랙션 타입은 Discord의 번호를 씁니다. 2는 명령어, 3은 컴포넌트, 4는 자동 완성, 5는 모달 제출입니다.

4007처럼 새 세션을 시작하게 되는 종료 뒤에는 봇이 `HTTPSettings.identify_spacing`초를 기다린 뒤에야 다시 identify합니다. 기본값은 Discord가 요구하는 대로 5초입니다. 테스트에서는 `Bot(http_settings=HTTPSettings(identify_spacing=0))`로 이 대기를 건너뜁니다.

`bostie.testing.payloads`는 Discord 문서의 예제를 바탕으로, 새 ID를 붙여 Discord의 페이로드를 만듭니다.

- 명령어와 자동 완성에는 `command(name, *options)`, `option(name, type, value, focused=False)`, `subcommand(...)`, `group(...)`, `resolved()`
- 사용자 명령어와 메시지 명령어에는 `context_menu(type, name, target_id, resolved)`
- 컴포넌트와 모달에는 `button(custom_id)`, `select(custom_id, *values)`, `modal(custom_id)`
- 이벤트에는 `message(...)`, `guild(...)`, `member(...)`, `user(...)`, `role(...)`, `channel(...)`, `voice_channel(...)`, `stage_channel(...)`, `voice_state(...)`. 각각 키워드 인자로 필드를 덮어쓸 수 있습니다
- bostie에 포함된 문서화된 페이로드에는 `fixture(name)`, 새 ID에는 `snowflake()`

## 봇이 보낸 것 확인하기 { #checking-what-the-bot-sent }

`fake.requests`는 모든 REST 요청을 `Recorded`로 나열합니다. 태스크처럼 봇이 스스로 보내는 요청에는 `fake.expect_request(method, route_prefix)`를 쓰면, 다음에 일치하는 요청을 받을 future를 반환합니다. `Recorded`에는 `method`, `path`(API 기본 경로 기준), `query`, `headers`, `json`이 있고, 업로드라면 `parts`도 있습니다. 인터랙션에 대한 `Recorded` 응답은 위에서처럼 `json`에 콜백 본문이 들어 있습니다. `fake.gateway_received`는 봇이 게이트웨이로 보낸 페이로드의 큐이고, `fake.connections`는 게이트웨이 연결 목록으로, 각각 `received` 페이로드, `shard`, `close_code`를 가진 `Connection` 객체입니다.

`fake.cdn_file(path, data)`는 봇이 내려받을 첨부 파일이나 에셋을 위해, 반환하는 CDN URL에서 `data`를 제공합니다. `fake.cdn_requests`는 그 다운로드를 기록합니다.

페이크는 이 라우트들에 필요한 상태를 유지하며, 테스트에서 그 상태를 읽거나 미리 채워 둘 수 있습니다. `fake.guilds`, `fake.channels`, `fake.users`, `fake.members`, `fake.messages`, `fake.commands`, `fake.guild_commands`가 있고, 명령어 동기화 횟수는 `fake.put_count`에 있습니다. `fake.ready_guilds`는 `READY`에 나열할 서버 ID를 설정합니다.

## 실패와 타이밍 { #failures-and-timing }

- `fake.fail(method, route_prefix, status, json)`은 다음에 일치하는 요청에 에러로 응답하며, 페이크가 제공하지 않는 라우트를 대신할 때도 씁니다.
- `fake.rate_limit(method, route_prefix, scope="user", retry_after=0.1)`은 429로 응답합니다.
- `fake.answer(method, route_prefix, body, content_type)`은 JSON이 아닌 본문으로 응답합니다.
- `fake.hold(method, route_prefix)`는 반환된 `asyncio.Event`를 설정할 때까지 요청을 기다리게 합니다. `fake.hold_identify(shard)`와 `fake.hold_hello()`는 게이트웨이 핸드셰이크에서 같은 일을 합니다.
- `await settle()`은 이벤트 루프에 몇 번 제어를 넘겨, 방금 한 동작으로 예약된 콜백이 실행되게 합니다.

봇이 리스너에서 하는 일을 기다리려면, sleep 대신 두 번째 테스트처럼 봇이 [직접 정의한 이벤트를 디스패치](events.md#events-of-your-own)하게 하고 그 이벤트를 기다립니다.

`FakeDiscord(compress=True)`는 게이트웨이를 zstd로 압축하며, Python 3.14의 `compression.zstd`가 필요합니다. `heartbeat_interval`은 페이크가 요청하는 하트비트 간격을 설정하며, 기본값은 Discord와 같은 41 250 ms입니다.

## 음성 { #voice }

`FakeDiscord(voice=True)`는 음성 채널과 스테이지 채널을 추가하고, 음성 상태 업데이트에 응답하며, 음성 게이트웨이와 UDP 서버를 `fake.voice`로 실행합니다. 그래서 `ctx.voice.connect()`와 `vc.play(...)`를 이 페이크에 대고 실행할 수 있습니다. DAVE는 지원하지 않으며, 이 부분은 bostie의 Rust 테스트가 다룹니다. 테스트에서는 다음을 할 수 있습니다.

- `await fake.next_voice_state_update()`, `await fake.voice.next_identify()`, 복호화된 RTP 패킷을 `Packet` 객체로 반환하는 `await fake.voice.wait_for_packets(count)`로 봇이 보낸 것을 기다립니다. 각 음성 연결은 `fake.voice.sessions`에 있는 `VoiceSession`으로, `VoiceSocket` 위에서 동작하며, `fake.voice.op(op, d)`는 가장 최근 연결에 임의의 음성 opcode를 보냅니다.
- 다른 사용자처럼 행동합니다. `fake.voice.clients_connect(user_ids)`, `fake.voice.client_disconnect(user_id)`, `fake.voice.speaking(user_id, ssrc, flags)`, 봇에 Opus 패킷을 보내는 `fake.voice.send_audio(...)`, RTCP 패킷을 보내는 `fake.voice.send_rtcp(packet)`, 고장 난 서버처럼 바이트를 암호화하지 않고 보내는 `fake.voice.send_datagram(data)`를 사용합니다.
- `fake.move_bot(channel_id)`와 `fake.kick_bot()`으로 모더레이터처럼 봇을 옮기거나 연결을 끊습니다.
- `fake.hold_voice_state()`로, 채널이 가득 찼을 때 Discord가 하듯 다음 음성 상태 업데이트에 반환된 `asyncio.Event`가 설정될 때까지 응답하지 않습니다.
- 봇에 대해 보내는 음성 상태에 쓸 `fake.mute`, `fake.deaf`, `fake.suppress`와, 참가할 때 Discord가 보내는 두 이벤트의 순서를 정하는 `voice_order`를 설정합니다.
