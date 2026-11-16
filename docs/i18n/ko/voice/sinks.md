---
source: e89423f86355edd642290a66c8608f48553e0ff6
description: "싱크로 원시 PCM이나 Opus 오디오를 Discord 음성 통화에 씁니다. 포맷, 백프레셔, 마커, 크로스페이드, 버퍼 설정, 언더런 카운터를 다룹니다."
---

# 싱크와 원시 오디오

싱크는 Python이 오디오를 쓰고 Rust 믹서가 20 ms씩 읽어 가는, 크기가 제한된 버퍼입니다. 모든 트랙과 큐는 싱크를 통해 재생됩니다. 직접 만들어 내거나 다른 곳에서 받은 오디오라면 싱크를 직접 여세요. 신시사이저, 텍스트 음성 변환(TTS) 엔진, 다른 프로그램의 스트림 같은 경우입니다.

```python
--8<-- "docs/snippets/voice/tone_sink.py"
```

## 포맷 { #formats }

`vc.sink(format)`은 쓸 오디오의 형식인 `voice.Format`에 맞는 `voice.Sink`를 반환합니다.

- `voice.PCM(rate=48000, channels=2, sample="s16le")`: 인터리브된 리틀 엔디언 샘플입니다. `"s16le"`(16비트 정수) 또는 `"f32le"`(32비트 부동소수점), 모노 또는 스테레오, 8000~192 000 Hz를 지원합니다. bostie는 Rust에서 48 kHz로 리샘플링합니다. 샘플레이트는 48 kHz와 최대 960개 샘플 단위의 블록으로 나누어떨어져야 하는데, 흔히 쓰는 샘플레이트는 모두 이 조건을 만족합니다.
- `voice.Opus()`: 한 번 쓸 때 Opus 패킷 하나입니다. 48 kHz 스테레오의 20 ms 패킷은 싱크가 필터 없이 볼륨 1로 혼자 재생되는 동안 쓴 그대로 전송되고, 그 밖의 경우에는 디코딩해서 믹싱합니다.

`voice.OpusEncoder(pcm_format, encoder=EncoderSettings())`는 Python 프로세스 안에서 PCM을 이런 패킷으로 인코딩합니다. 한 번 인코딩해서 여러 싱크에 보낼 오디오에 씁니다. `encode(data)`는 지금까지 완성된 패킷을 모두 반환하고, `flush()`는 나머지를 무음으로 채웁니다. 따라갈 채널이 없으므로 비트레이트 기본값은 64 kbps입니다.

## 쓰기 { #writing }

- `await sink.write(data)`는 오디오를 대기열에 넣고, 싱크에 이미 `buffer`초 분량이 쌓여 있으면 기다립니다. 이 기다림이 생산자를 실시간에 맞춰 주는 백프레셔입니다.
- `sink.write_nowait(data)`는 청크 전체를 한 번에 대기열에 넣거나, `BufferFull`을 발생시킵니다.
- `async with sink:`는 블록이 끝날 때 쓴 오디오가 모두 재생될 때까지 기다린 다음 싱크를 닫습니다. 블록 안에서 예외가 발생하면 대신 대기 중인 오디오를 버립니다.
- `await sink.drain()`은 지금까지 쓴 오디오가 모두 재생될 때까지 기다립니다. 일부가 버려졌다면 `False`입니다.
- `sink.clear()`는 대기 중인 오디오를 버립니다. `clear(until=marker)`는 마커 앞부분만, `clear(since=marker)`는 마커 뒷부분만 버립니다.
- `sink.close()`는 대기 중인 오디오를 버리고 싱크를 닫습니다. 닫힌 싱크에 쓰면 `SinkClosed`가 발생합니다.

`sink.mark()`는 지금까지 쓴 오디오 뒤에 `Marker`를 놓고 반환합니다. `await marker`는 그 앞의 오디오가 모두 재생되면 `True`를, 그 전에 일부가 버려졌다면 `False`를 반환합니다. `marker.done`은 결과가 이미 정해졌는지 알려 줍니다. 플레이어는 마커로 트랙이 쓰인 시점이 아니라 실제로 끝난 시점을 알 수 있습니다.

## 설정 { #settings }

`vc.sink`는 다음 키워드 인수를 받습니다. `vc.play`와 `voice.Queue`도 `buffer`와 `stall`을 받습니다.

| 설정 | 기본값 | 의미 |
|---|---|---|
| `buffer` | `Defaults.sink_buffer`, 0.2 | `write`가 기다리기 전까지 싱크가 담아 두는 시간(초), 0.02~60 |
| `stall` | `Defaults.stall`, `"hold"` | 재연결이나 DAVE 전환 중처럼 오디오를 보낼 수 없을 때의 동작: `"hold"`는 오디오를 보관했다가 나중에 재생하고, `"drop"`은 실시간으로 버립니다. `"drop"`은 뒤처지면 안 되는 라이브 오디오용입니다 |
| `volume` | 1.0 | 샘플에 곱하는 값. 재생 중에는 `sink.volume`으로 바꿉니다 |
| `filters` | `Filters()` | [효과](playback.md#volume-and-filters). 재생 중에는 `sink.filters`로 바꿉니다 |
| `after` | `None` | 같은 연결의 다른 싱크. 그 싱크가 닫히거나, 마지막 `drain()` 전에 쓴 오디오를 다 재생할 때까지 이 싱크는 소리를 내지 않습니다 |
| `crossfade` | 0.0 | `after`와 함께 쓰며, 몇 초 일찍 시작해 겹치면서 페이드할지 정합니다. 그만큼 싱크가 더 버퍼링합니다 |

큐는 `after`와 `crossfade`로 빈틈없이 재생합니다. 현재 트랙이 재생되는 동안 다음 트랙의 싱크가 채워지고, 정확히 그 샘플에서 넘겨받습니다.

## 상태와 카운터 { #state-and-counters }

`sink.queued`는 대기 중인 오디오의 길이(초), `sink.passthrough`는 마지막 패킷이 쓴 그대로 나갔는지, `sink.closed`는 싱크가 닫혔는지를 나타냅니다. `sink.stats()`는 `SinkStats`를 반환합니다.

| 필드 | 값 |
|---|---|
| `sent` | 보낸 오디오의 길이(초) |
| `position` | 마커가 세는 방식으로 센 재생 위치(초) |
| `underruns` | `drain()` 밖에서 재생 중에 싱크가 비어 버린 횟수. 생산자가 뒤처졌다는 뜻입니다 |
| `held_ticks` | 오디오를 보낼 수 없어 싱크가 붙잡혀 있던 틱 수 |
| `dropped` | `"drop"` 멈춤 동작으로 버려진 길이(초) |
| `passthrough_packets` | 디코딩 없이 쓴 그대로 보낸 패킷 수 |

눈여겨볼 값은 언더런입니다. `buffer`초만큼 앞서 나가지 못하는 생산자는 너무 느리거나, 이벤트 루프가 막혀 있는 것입니다. `vc.sinks`는 연결에 열려 있는 싱크를 나열합니다.
