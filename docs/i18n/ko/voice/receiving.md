---
source: 0d8bbdce2b1aa0ef1ee6009ade35eb117f7d7909
description: "Discord 음성을 화자별 PCM, Opus, RTP 패킷으로 수신하고, 말하기를 감지해 발화를 나누며, 통화를 중계하고 Ogg나 WAV로 녹음합니다."
---

# 수신과 녹음

![음파가 도착하는 동안 마이크를 들고 있는 Bostie](../assets/mascot/bostie-receiving.webp){ align=right width="180" loading=lazy }

`vc.receive()`는 리시버를 엽니다. 리시버는 봇이 듣는 오디오를 순회하는, 크기가 제한된 비동기 이터레이터입니다. 화자마다 따로 도착하며, Rust에서 복호화하고 순서를 바로잡은 뒤 디코딩합니다. 한 연결에서 여러 리시버를 열 수 있고, 각 리시버는 자신만의 포맷과 필터를 가집니다.

```python
--8<-- "docs/snippets/voice/listening.py"
```

## 포맷 { #formats }

`vc.receive(format)`은 `voice.ReceiveFormat`에 맞는 `voice.Receiver`를 반환합니다.

| `vc.receive(format)` | 항목 | 용도 |
|---|---|---|
| `voice.PCM(rate=48000, channels=2, sample="s16le")` | 그 포맷으로 디코딩된 샘플을 담은 `Frame`. 샘플레이트는 `voice.DECODE_RATES` 중 하나로, 8, 12, 16, 24, 48 kHz | 듣기, 음성 인식, 음량 측정 |
| `voice.Opus()` | `data`가 Opus 패킷 하나인 `Frame` | 디코딩 없이 녹음하거나 전달하기 |
| `voice.Packets()` | 지터 버퍼를 거치기 전, 복호화되어 도착한 순서대로인 `Packet` | 직접 만든 지터 버퍼, RTP 헤더 확장 |

`Frame`에는 `user_id`, `ssrc`, RTP `timestamp`, 리시버의 48 kHz 클록 기준인 `offset`과 `samples`, `received_at`, `data`, `lost`(손실된 패킷을 감추려고 Opus가 만들어 낸 오디오면 참)가 있고, PCM이면 프레임의 RMS를 dBFS로 나타낸 `level`도 있습니다. 클록은 리시버가 열릴 때 시작하므로, 서로 다른 사용자의 프레임을 `offset`으로 맞춰 정렬할 수 있습니다. `Packet`에는 `user_id`, `ssrc`, `sequence`, `timestamp`, `received_at`, `opus` 페이로드, RTP 헤더 `extensions`가 있습니다.

## 들을 사용자 고르기 { #choosing-whom-to-hear }

- `users=[...]`는 지정한 사용자만 남기고, `exclude=[...]`는 일부를 뺍니다.
- `include_self=True`는 봇 자신의 오디오를 추가합니다.
- `mix=True`(PCM과 Opus)는 모두를 믹싱한 스트림 하나를 전달하며, `user_id`는 `None`입니다.
- `markers=True`는 프레임 사이에 `SpeechStart`, `SpeechStop`, `UserLeave` 이벤트를 넣습니다. 각 이벤트에는 같은 클록 기준의 `offset`이 있으므로, 소비자는 시간을 직접 재지 않고도 말이 어디서 시작하고 끝나는지 알 수 있습니다.

## 설정 { #settings }

`vc.receive`는 다음 키워드 인수를 받습니다. `Packets()`에는 지터 버퍼가 없으므로 `jitter`를 받지 않습니다.

| 설정 | 기본값 | 의미 |
|---|---|---|
| `buffer` | `Defaults.receive_buffer`, 2.0 | 느린 소비자를 위해 리시버가 담아 두는 항목의 길이(초), 0.02~60 |
| `overflow` | `Defaults.overflow`, `"drop_oldest"` | 버퍼가 가득 찼을 때 가장 오래된 항목을 버릴지, 아니면 `"drop_newest"`로 가장 새로운 항목을 버릴지 |
| `jitter` | `Defaults.jitter`, 0.1 | 늦게 오는 패킷을 기다렸다가 계속 진행하기까지의 시간(초), 0~1. PCM과 Opus만 해당 |
| `stall` | `Defaults.receive_stall`, `"wait"` | 연결이 재연결될 때: `"wait"`는 리시버를 열어 두고, `"end"`는 끝냅니다 |

## 읽기 { #reading }

`async for item in rx`는 항목을 기다리며, 리시버가 끝나면 멈춥니다. `async with rx:`는 블록이 끝날 때 리시버를 닫습니다.

- `rx.get_nowait()`는 다음 항목을 반환합니다. 대기 중인 항목이 없으면 `voice.Empty`를, 리시버가 끝났으면 `voice.ReceiverClosed`를 발생시킵니다.
- `rx.queued`는 가장 오래된 대기 항목이 기다린 시간, 즉 소비자의 지연입니다.
- `rx.clear()`는 대기 중인 항목을 모두 버리고 실시간으로 이어 갑니다.
- `await rx.mark()`는 호출 전에 받은 오디오가 대기열에 들어가거나 버려지면 `True`를 반환하고, 1.2초에 지터의 두 배를 더한 시간이 지나면 `False`를 반환합니다. 기다림 하나를 취소해도 다른 기다림의 마크는 남습니다.
- `rx.stats()`는 `ReceiverStats`를 반환합니다. 전달된 항목 수 `delivered`, 넘쳐서 버려진 수 `dropped`, 도중에 손실된 패킷 수 `lost`가 담깁니다.
- `rx.close()`는 수신을 멈춥니다. `voice.ClosedReason`인 `rx.closed_reason`은 리시버가 끝난 이유를 알려 줍니다: `CLOSED`, `DISCONNECTED`, `MOVED`(봇이 채널을 옮김), `RECONNECTING`(`stall="end"`일 때).

관리자가 봇을 헤드셋 음소거할 수 있으며, 그러면 Discord는 봇에 오디오를 보내지 않습니다. bostie는 봇이 헤드셋 음소거된 상태에서 리시버가 열릴 때와, 리시버가 열려 있는 동안 봇이 헤드셋 음소거될 때 경고를 로그에 남깁니다.

## 중계하기 { #relaying }

리시버와 싱크를 직접 연결해 한 통화의 오디오를 다른 통화로 옮길 수 있습니다.

```python
--8<-- "docs/snippets/voice/relay.py"
```

믹싱된 프레임에는 `user_id`, `ssrc`, `timestamp`가 없으며, 믹스에는 마커를 실을 수 없습니다. 손실된 Opus 프레임은 넘겨줄 패킷이 없으므로 `data`가 비어 있습니다. `relay_speaker`처럼 Opus 싱크에 쓰기 전에 건너뛰세요. 양쪽 모두 기본값이 48 kHz 스테레오 16비트 PCM이므로, PCM 리시버는 기본 싱크에 바로 쓸 수 있습니다.

## 말하기 감지 { #speech }

`speech_activity`가 켜져 있는 동안 모든 연결에서 Rust가 말하기를 감지합니다. 사용자는 첫 오디오와 함께 말하기 시작하고, `speech_hangover`초(기본값 0.2) 동안 무음이 이어지면 말을 멈춘 것으로 봅니다. `vc.speaking`에는 지금 말하는 중인 사용자가 담기고, `SpeechStart`와 `SpeechStop` 이벤트가 리스너에 이를 알리며, `markers=True`로 연 리시버는 스트림 안에 이를 싣습니다. `speech_activity`가 꺼져 있고 열린 리시버도 없으면 bostie는 들어오는 오디오를 복호화조차 하지 않습니다.

`voice.utterances(rx, min_silence=0.8, max_length=30.0)`는 PCM 리시버의 프레임을 `Utterance`로 묶습니다. `Utterance`는 한 사용자가 말을 시작해서 끝낼 때까지의 발화로, 리시버 포맷의 `data`, `offset`, `duration`, 손실된 길이(초)인 `lost`를 담습니다. 발화는 `min_silence`초 동안 말이 없거나 `max_length`초에 이르면 끝납니다. `mix` 없이 `markers=True`로 연 PCM 리시버가 필요합니다. 또는 dBFS 단위의 `level`을 넘기면, 마커 대신 그 값으로 무엇을 말로 볼지 정합니다.

## 녹음 { #recording }

```python
--8<-- "docs/snippets/voice/call_recording.py"
```

`voice.Recorder(vc, path)`는 사용자마다 각자의 파일에 녹음하고, `mix=True`이면 통화 전체를 파일 하나에 녹음합니다. 경로는 `{guild_id}`와 `{user_id}`를 쓰는 템플릿입니다. 사용자별 파일에는 필수이고 `mix=True`에서는 거부되며, 확장자가 컨테이너를 정합니다.

- `.ogg` 또는 `.opus`는 Ogg Opus로 씁니다. 사용자별 녹음에서는 받은 Opus 패킷을 디코딩 없이 그대로 씁니다.
- `.wav`는 레코더의 `format`에 맞춰 16비트 PCM으로 쓰며, 기본값은 48 kHz 스테레오입니다. 샘플레이트는 `voice.DECODE_RATES` 중 하나입니다.

기본값인 `align=True`이면 빈 구간을 무음으로 채우므로, 모든 파일이 녹음 시작 시점에 시작해 서로 정렬됩니다. `users`, `exclude`, `include_self`는 리시버에서처럼 녹음할 사용자를 고릅니다. 디스크가 잠시 멈출 수 있으므로 `buffer`(30초)와 `jitter`(0.3초)는 리시버의 기본값보다 큽니다. 녹음은 `max_duration`초가 지나거나, 파일 크기가 `max_size`바이트에 이르거나, 연결이 끝나면 스스로 끝납니다.

`await recorder.stop()`은 이미 들은 오디오를 다 쓴 뒤 녹음을 끝내고, 사용자 ID를 `Path`에 매핑한 dict로 파일을 반환합니다. 믹싱된 파일의 키는 `None`입니다. `await recorder.wait()`는 녹음이 스스로 끝나기를 기다립니다. `recorder.files`는 지금까지 쓴 파일을 나열하고, `recorder.stats()`는 리시버의 카운터를 반환합니다. 여기서 `dropped`는 디스크가 `buffer`보다 오래 뒤처졌다는 뜻입니다.

위 코드처럼, 봇이 녹음하고 있다는 사실을 통화 참가자에게 알리세요.
