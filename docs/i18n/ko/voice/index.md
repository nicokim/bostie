---
source: 4108c4a61370aa8ef893c1d65371c3d654e43c09
description: "bostie로 Discord 음성 채널과 스테이지 채널에 참가합니다. 연결 설정, DAVE 종단 간 암호화, 음성 이벤트, 외부 음성 엔진을 다룹니다."
---

# 음성 연결

![헤드폰을 끼고 귀 기울이는 Bostie](../assets/mascot/bostie-listen.webp){ align=right width="180" loading=lazy }

bostie의 음성 엔진은 Rust 코어에서 동작합니다. 음성 WebSocket, UDP 전송, 20 ms 송신 클록, 디코딩과 리샘플링, 믹서, Opus 인코딩과 디코딩, 전송 암호화, DAVE 종단 간 암호화가 모두 여기에 들어 있습니다. Python은 엔진에 입장, 재생, 수신을 요청할 뿐이며, 타이밍은 이벤트 루프에 전혀 의존하지 않습니다. 다만 Python이 각 싱크의 버퍼를 계속 채워 주어야 합니다.

음성에는 `Intents.GUILD_VOICE_STATES`가 필요하며, `Intents.DEFAULT`에 포함되어 있습니다. 봇은 서버마다 음성 연결을 최대 하나만 가집니다.

```python
--8<-- "docs/snippets/voice/voice_connection.py"
```

## 연결하기 { #connecting }

`await ctx.voice.connect()`는 명령어를 실행한 사용자가 있는 음성 채널이나 스테이지 채널에 들어가고, 오디오가 양방향으로 흐를 수 있게 되면 반환합니다. 사용자의 채널은 캐시에서 찾고, 서버가 캐시에 없으면 Discord에 물어봅니다. 사용자가 그 서버의 어느 음성 채널에도 없거나 인터랙션이 개인 메시지(DM)에서 왔다면 `NotInVoice`를 발생시킵니다. `ctx.voice.current`는 그 서버에서의 봇 연결이며, 없으면 `None`입니다.

핸들러 밖에서는 `await bot.voice.connect(channel_id)`로 ID를 지정해 채널에 들어갑니다. 봇이 이미 연결된 서버에서는 연결을 옮기고, 넘긴 옵션만 적용합니다. `bot.voice.get(guild_id)`와 `bot.voice.connections`는 열려 있는 연결을 반환하고, `await bot.voice.close()`는 `bot.close()`가 하듯이 모든 연결에서 나갑니다.

`timeout`초 안에 준비되지 않은 입장은 이유가 `TIMEOUT`인 `VoiceConnectError`를 발생시킵니다. 가득 찬 채널이나 봇이 접속할 수 없는 채널로의 입장 요청에는 Discord가 응답하지 않기 때문입니다. 그 밖의 실패는 이유, 종료 코드, Discord의 메시지를 담은 `VoiceConnectError`를 발생시킵니다.

## 설정 { #settings }

`Bot(voice=voice.Defaults(...))`는 모든 연결이 시작할 때 쓰는 값을 정하고, `connect()`는 한 번의 호출에 쓸 연결 설정을 받습니다.

| `voice.Defaults` 필드 | 기본값 | 의미 |
|---|---|---|
| `self_mute`, `self_deaf` | `False` | 봇 자신의 마이크 음소거와 헤드셋 음소거 |
| `timeout` | 10.0 | 입장, 이동, 수정을 기다리는 시간(초) |
| `server_change_delay` | 0.0 | Discord를 따라 새 음성 서버로 옮기기 전에 기다리는 시간(초) |
| `encoder` | `EncoderSettings()` | Opus 인코더. [오디오 재생](playback.md#encoder) 참고 |
| `speaking_flags` | `MICROPHONE` | 봇이 재생할 때 알리는 `voice.SpeakingFlags` |
| `stage` | `"listen"` | 스테이지에서의 역할: `"listen"`, 발언을 요청하는 `"request"`, 또는 `"speak"` |
| `speech_hangover` | 0.2 | 화자가 말을 멈춘 것으로 보기 전의 무음 시간(초), 0.02~10 |
| `speech_activity` | `True` | 말하기 시작과 멈춤 이벤트를 발생시킬지 여부 |
| `stall_after` | 2.0 | `MediaStalled`가 보고하기 전까지 미디어 흐름이 멈춰 있어도 되는 시간(초), 최소 0.02 |
| `sink_buffer`, `stall` | 0.2, `"hold"` | 모든 [싱크](sinks.md)의 기본값 |
| `receive_buffer`, `jitter`, `overflow`, `receive_stall` | 2.0, 0.1, `"drop_oldest"`, `"wait"` | 모든 [리시버](receiving.md)의 기본값 |
| `crossfade`, `history` | 0.0, 0 | 모든 [큐](playback.md#queues)의 기본값 |

`connect()`는 `self_mute`, `self_deaf`, `timeout`, `server_change_delay`, `encoder`, `speaking_flags`, `stage`, `speech_hangover`, `speech_activity`, `stall_after`를 키워드 인수로 받습니다. 생략하거나 `None`을 넘긴 인수는 기본값을 유지합니다. `connect_external()`은 앞의 세 개를 받고, `move()`는 `timeout`을, `edit()`는 `self_mute`, `self_deaf`, `timeout`을 받습니다. 잘못된 값은 `Defaults`를 만들 때 `ValueError`를 발생시킵니다. 문자열 설정의 리터럴 타입은 `voice.StageRole`, `voice.SinkStall`, `voice.ReceiveStall`, `voice.Overflow`, `voice.Sample`, `voice.OpusApplication`, `voice.OpusBandwidth`, `voice.OpusSignal`입니다. `ctx.voice`는 `voice.ContextVoice`이고 `bot.voice`는 `voice.VoiceManager`입니다.

## 연결 { #the-connection }

| `VoiceConnection` | 값 |
|---|---|
| `guild_id`, `channel_id` | 연결된 위치 |
| `state` | `voice.State`: `CONNECTING`, `READY`, `RESUMING`, `RECONNECTING`, `DISCONNECTED` 중 하나 |
| `members` | 통화에 있는 다른 사용자들의 ID |
| `speaking` | 지금 말하는 중인 사용자와, 각자 말하기 시작한 `time.monotonic()` 값의 매핑 |
| `ssrcs` | 통화의 각 SSRC와 그 사용자 ID의 매핑 |
| `self_mute`, `self_deaf` | Discord가 적용한 봇 자신의 마이크 음소거와 헤드셋 음소거 |
| `mute`, `deaf`, `suppress` | 모더레이터가 봇을 마이크 음소거하거나 헤드셋 음소거했는지, 또는 이 스테이지에서 봇이 발언할 수 없는지 여부 |
| `latency` | WebSocket과 UDP의 왕복 시간(초)을 담은 `Latency`, 측정 전에는 `None` |
| `dave` | 통화의 종단 간 암호화. 아래 참고 |
| `sinks` | 열려 있는 싱크, 오래된 것부터 |
| `encoder`, `speaking_flags`, `speech_hangover`, `speech_activity`, `stall_after` | 연결된 동안 변경 가능 |

- `await vc.move(channel_id)`는 서버의 다른 음성 채널이나 스테이지 채널로 옮기고, 그곳에서 미디어가 흐르기 시작하면 반환합니다.
- `await vc.edit(self_mute=..., self_deaf=...)`는 봇 자신의 마이크 음소거나 헤드셋 음소거를 바꾸고, 넘기지 않은 쪽은 그대로 둡니다. Discord가 적용하면 반환합니다.
- `await vc.stage("request")`는 스테이지에서 발언을 요청하고, `"speak"`는 (권한이 있으면) 발언하며, `"listen"`은 청중으로 돌아갑니다.
- `await vc.wait_until(voice.State.READY)`는 특정 상태를 기다리며, 그 전에 연결이 끝나면 `VoiceNotConnected`를 발생시킵니다.
- `vc.reconnect()`는 음성 WebSocket을 끊고 새 WebSocket에서 재개합니다.
- `vc.stop()`은 모든 싱크를 닫으므로 아무것도 재생되지 않습니다.
- `await vc.disconnect()`는 채널에서 나가고, 연결이 끝나면 반환합니다. 길어도 2초 안에 반환합니다.

끝난 연결에서 호출하면 `VoiceNotConnected`가 발생합니다.

연결은 스스로 복구됩니다. 끊긴 WebSocket은 재개하고, 잃어버린 세션은 다시 연결하며, 모더레이터가 봇을 옮기면 새 채널로 따라가서 `Moved` 이벤트를 보냅니다. 모더레이터가 봇의 연결을 끊거나 채널을 삭제하면 연결은 이유 `REMOVED`로 끝납니다. bostie는 봇이 서버에서 마이크 음소거된 상태나 스테이지에서 발언이 제한된 상태로 재생할 때, 그리고 봇이 헤드셋 음소거된 상태에서 리시버가 기다릴 때 경고합니다.

## 종단 간 암호화 { #end-to-end-encryption }

bostie는 Discord의 음성 종단 간 암호화인 DAVE를 별도 설정 없이 지원합니다. 통화의 암호화 그룹에 참여하고, 보내는 것은 암호화하고 받는 것은 복호화합니다. `vc.dave`는 그 상태를 `voice.Dave`로 알려 줍니다.

- `protocol_version`은 사용 중인 DAVE 프로토콜이며, 없으면 0입니다.
- `epoch`는 그룹 멤버가 바뀐 횟수입니다.
- `privacy_code`는 Discord 클라이언트가 통화에 표시하는 코드입니다.
- `await vc.dave.verification_code(user_id)`는 다른 사용자와 별도 경로로 비교할 코드를 반환합니다. 이 코드로 중간에 다른 누군가가 끼어 있지 않은지 확인합니다.

`DaveTransition` 이벤트가 각 변경을 알립니다. DAVE를 요구하는 채널에 DAVE를 지원하지 않는 클라이언트가 들어가면 이유 `DAVE_REQUIRED`로 닫히고, DAVE 단계가 실패하면 `DaveError`가 발생하거나 연결이 `DAVE_FAILED`로 끝납니다.

## 이벤트 { #events }

음성 연결은 `@bot.listen`으로 이벤트를 보냅니다. 각 이벤트는 `guild_id`를 가진 `voice.VoiceEvent`입니다. `DISCONNECTED`로 바뀌는 `StateChange`에는 `voice.DisconnectReason`이 담깁니다: `REQUESTED`(`disconnect()`가 실행됨), `REMOVED`(모더레이터가 봇의 연결을 끊거나 채널을 삭제함), `CALL_TERMINATED`, `RATE_LIMITED`, `TIMEOUT`, `SESSION_INVALID`, `AUTHENTICATION_FAILED`, `DAVE_REQUIRED`, `DAVE_FAILED`, `PROTOCOL_ERROR`, `CLOSED`(봇이 종료됨).

| 이벤트 | 필드 | 발생 시점 |
|---|---|---|
| `StateChange` | `old`, `new`, `reason`, `code`, `detail` | 연결 상태가 바뀌었을 때 |
| `Moved` | `old_channel_id`, `new_channel_id`, `by_us` | `move()`나 모더레이터에 의해 봇이 옮겨졌을 때 |
| `DaveTransition` | `protocol_version`, `epoch`, `transition_id` | 종단 간 암호화가 바뀌었을 때 |
| `MediaStalled`, `MediaResumed` | `direction`(`voice.Direction.SEND` 또는 `RECEIVE`), `duration` | 송신이나 수신이 멈췄거나 다시 시작되었을 때 |
| `UserJoin`, `UserLeave` | `user_id` | 사용자가 통화에 들어오거나 나갔을 때 |
| `SpeakingUpdate` | `user_id`, `ssrc`, `flags` | 사용자가 자신의 SSRC와 말하기 플래그를 알렸을 때 |
| `SpeechStart`, `SpeechStop` | `user_id`, `ssrc`, `at`, 멈출 때는 `duration`도 | 사용자가 말하기 시작하거나 멈췄을 때 |
| `RtcpPacket` | `packet_type`, `count`, `ssrc`, `user_id`, `reports`, `data` | 음성 서버가 RTCP 패킷을 보냈을 때. `reports`의 각 `ReceptionReport`에는 스트림 하나의 `fraction_lost`, `lost`, `highest_sequence`, `jitter`, `last_sender_report`, `delay`가 담깁니다 |

이 이벤트들은 `bostie.voice`에 있습니다. `SpeechStart`와 `SpeechStop`은 `speech_activity`가 켜져 있을 때만 발생합니다.

## 멈춘 미디어 { #stalled-media }

```python
--8<-- "docs/snippets/voice/media_stalls.py"
```

`direction`이 `SEND`인 `MediaStalled`는 재연결이나 DAVE 전환 중에 오디오가 싱크에서 기다리면서 `stall_after`초 동안 나가지 못할 때 발생합니다. `RECEIVE`이면 패킷이 열린 리시버에 도착했지만 그 시간 동안 하나도 열거나 재생할 수 없었다는 뜻입니다. 미디어가 다시 흐르면 `MediaResumed`가 이어서 발생하며, 그 `duration`은 멈춤이 시작된 시점부터 셉니다. `stall_after`는 `voice.Defaults`, `connect(stall_after=...)`, `vc.stall_after`에서 따로 정하지 않으면 2초입니다.

## 빈 채널에서 나가기 { #leaving-an-empty-channel }

봇은 연결을 끊기 전까지 채널에 머무릅니다. 마지막 사용자가 나갈 때 함께 나가려면 다음과 같이 합니다.

```python
--8<-- "docs/snippets/voice/leave_when_alone.py"
```

## 에러 { #errors }

음성 에러는 `VoiceError`입니다: `VoiceConnectError`(입장이 준비되기 전에 끝남. `reason`, `code`, `detail`), `NotInVoice`, `VoiceNotConnected`, `AlreadyConnected`, `VoiceQueueFull`(연결을 기다리는 명령이 너무 많음), `DaveError`, 그리고 오디오용 `SourceError`, `SinkClosed`, `BufferFull`, `Empty`, `ReceiverClosed`가 있습니다. 오디오 에러는 다음 페이지들에서 설명합니다.

## 외부 음성 엔진 { #external-voice-engines }

```python
--8<-- "docs/snippets/voice/external_engine.py"
```

`await bot.voice.connect_external(guild_id, channel_id)`는 다른 곳에서 동작하는 음성 엔진을 위해 채널에 들어가고, 엔진이 연결에 사용할 `state`(`VoiceStateUpdate`)와 `server`(`VoiceServerUpdate`)를 담은 `ExternalVoice`를 반환합니다. 이후 bostie는 그 서버에서 오디오를 보내지 않습니다. `async with external.updates() as updates:`는 그 서버의 이후 음성 서버 정보와 봇 자신의 음성 상태를 전달하고, `await external.disconnect()`는 채널에서 나갑니다. bostie가 자체 연결을 운영 중인 서버에서는 `AlreadyConnected`를 발생시키며, `self_mute`, `self_deaf`, `timeout`을 받습니다.

## 버전 { #versions }

`voice.versions()`는 빌드에 컴파일된 bostie, libopus, DAVE 라이브러리의 버전과 가장 높은 DAVE 프로토콜 버전을 반환합니다.
