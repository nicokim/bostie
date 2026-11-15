---
source: fa47131472a60ef5dbaa1eef411b4ec762c85146
---

# 오디오 재생

![음악에 맞춰 고개를 끄덕이는 Bostie](../assets/mascot/bostie-playback.webp){ align=right width="180" loading=lazy }

`await vc.play(source)`는 [음성 연결](index.md)에서 소스를 재생하고 곧바로 `Track`을 반환합니다. 여러 트랙은 동시에 재생되며 믹싱되고, `Queue`는 트랙을 하나씩 차례로 재생합니다.

```python
--8<-- "docs/snippets/voice/tracks.py"
```

## 소스 { #sources }

소스는 재생할 오디오입니다. 각 소스는 한 번만 쓸 수 있습니다. `play`가 소스를 열고, 끝난 소스를 다시 열면 `StateError`가 발생합니다. 같은 오디오를 두 번 재생하려면 `source.again()`으로 같은 오디오를 가진 새 소스를 만듭니다.

| 소스 | 재생하는 것 | 탐색 가능 |
|---|---|---|
| `voice.File(path)` | 로컬 파일 | 예 |
| `voice.Bytes(data, hint=None)` | 메모리에 있는 파일 내용. `hint`는 `"ogg"` 같은 확장자 | 예 |
| `voice.Url(url, headers=None)` | 스트리밍하는 HTTP 또는 HTTPS 미디어 직접 URL, 또는 HLS 플레이리스트 | 서버가 범위 요청을 받아들일 때. HLS는 아니요 |
| `voice.FFmpeg(args, executable="ffmpeg")` | ffmpeg 명령의 출력. bostie가 디코딩하지 못하는 포맷용 | 아니요 |
| `voice.Stream(items, format)` | `format` 형식의 PCM 청크나 Opus 패킷을 내놓는 비동기 이터러블 | 아니요 |

`File`, `Bytes`, `Url`은 Rust에서 디코딩합니다. 지원 포맷은 Ogg(Opus, Vorbis), WebM과 Matroska, MP4와 M4A(AAC), MP3, FLAC, WAV입니다. URL은 미디어를 삽입한 웹 페이지가 아니라 미디어 자체를 가리켜야 합니다. 서버와의 연결이 끊기면 `Url`은 0.5초, 1초, 2초 뒤에 최대 세 번 다시 연결하며, 서버가 범위 요청을 받아들이면 끊긴 지점부터 이어서 재생합니다. HLS 마스터 플레이리스트는 오디오 렌디션을 재생하고, 라이브 플레이리스트는 끝에서 세 세그먼트 앞부터 시작합니다. 암호화된 플레이리스트와 바이트 범위 세그먼트는 `SourceError`로 거부합니다. `FFmpeg`는 입력 인수를 넘겨 프로그램을 실행하고 그 출력에서 48 kHz 스테레오 16비트 PCM을 읽습니다. 프로그램이 `PATH`에 없거나 에러로 종료하면 `SourceError`를 발생시킵니다. 열거나 디코딩하거나 탐색할 수 없는 소스는 `SourceError`를 발생시키거나, 트랙을 이유 `ERROR`로 끝냅니다.

`opened`를 설정하는 `open()`을 호출한 뒤에는 소스에 `format`, `duration`(초, 알 수 없으면 `None`), `seekable`, `tags`가 생깁니다. `tags`는 파일에 적힌 `title`, `artist`, `album`을 담은 `voice.Tags`입니다.

```python
--8<-- "docs/snippets/voice/other_sources.py"
```

대부분의 Ogg Opus 파일과 WebM 파일처럼 48 kHz 스테레오, 20 ms 패킷의 Opus 오디오는 필터 없이 볼륨 1로 혼자 재생되는 동안 디코딩과 재인코딩 없이 그대로 전송됩니다. `track.passthrough`로 이 경우인지 알 수 있습니다.

## 트랙 { #tracks }

`vc.play(source, volume=1.0, filters=Filters(), meta=None)`는 시작 볼륨과 필터, 그리고 트랙과 함께 보관할 아무 값인 `meta`(예: 누가 요청했는지)를 받습니다. 트랙의 [싱크](sinks.md)에 쓸 `buffer`와 `stall`도 받습니다.

| `Track` | 동작 |
|---|---|
| `await track.wait()` | 끝날 때까지 기다리고 `TrackEnd`를 반환합니다. 기다림을 취소해도 트랙은 멈추지 않습니다 |
| `await track.started()` | 재생이 시작될 때까지 기다립니다. 그 전에 끝나면 `False` |
| `track.ended` | `TrackEnd`, 재생 중에는 `None` |
| `track.position` | 지금까지 재생한 소스의 길이(초) |
| `track.pause()`, `track.resume()`, `track.paused` | 오디오 공급을 멈추고 다시 시작합니다. 이미 버퍼에 들어간 오디오는 계속 재생됩니다 |
| `await track.seek(seconds)` | 특정 위치부터 이어서 재생합니다. 소스가 탐색할 수 없으면 `SourceError` |
| `track.volume`, `track.filters` | 재생 중에 바꾸면 즉시 적용됩니다 |
| `track.stop()` | 트랙을 끝냅니다 |
| `track.source`, `track.meta`, `track.sink` | 재생하는 소스, 직접 넣은 값, 트랙이 쓰는 싱크 |

`TrackEnd`에는 `track`, `reason`(`FINISHED`, `STOPPED`, `error`를 담은 `ERROR`, 또는 연결이 끝났을 때의 `CLEANUP`), `may_start_next`가 있습니다. `may_start_next`는 `FINISHED`나 `ERROR` 뒤에 참이며, 직접 만든 플레이어가 다음 트랙으로 넘어가야 하는지 알려 줍니다. `vc.stop()`은 모든 트랙을 끝냅니다.

## 볼륨과 필터 { #volume-and-filters }

`volume`은 샘플에 곱하는 값입니다. 0.5는 소리를 절반으로 줄이고, 1.0은 그대로 둡니다. `voice.Filters`는 오디오가 재생될 때 Rust가 적용하는 효과를 설정합니다.

| `Filters` 필드 | 기본값 | 범위 |
|---|---|---|
| `equalizer` | `{}` | 최대 16개 밴드. 20~20 000 Hz의 중심 주파수를 dB 단위 게인에 매핑하며, 각 밴드의 폭은 1옥타브입니다 |
| `bass` | 0.0 | 100 Hz 미만의 게인(dB) |
| `speed` | 1.0 | 0.5~2.0, 음높이는 유지 |
| `pitch` | 1.0 | 0.5~2.0, 템포는 유지 |

게인은 -24~24 dB이며, 범위를 벗어난 값은 `ValueError`를 발생시킵니다.

## 큐 { #queues }

```python
--8<-- "docs/snippets/voice/queue_player.py"
```

`voice.Queue(vc)`는 트랙을 빈틈없이 이어서 재생하거나, `crossfade`초만큼 겹쳐 재생합니다. 연결의 설정을 기본값으로 하는 `volume`, `filters`, `crossfade`, `history`와, 싱크의 `buffer`, `stall`을 받습니다.

- `queue.add(source, meta=None, index=None)`는 소스를 맨 끝에, 또는 다음 트랙들 중 `index` 위치에 넣고, 그 `Track`을 반환합니다.
- `queue.now`, `queue.upcoming`, `queue.history`는 각각 현재 트랙, 다음 트랙들, 그리고 재생이 끝난 트랙을 최대 `history`개까지 담습니다.
- `queue.skip()`은 현재 트랙을 끝냅니다. `queue.back()`은 기록의 마지막 트랙을 다시 재생한 다음, 현재 트랙을 처음부터 다시 재생합니다.
- `queue.move(track, index)`, `queue.remove(track)`, `queue.shuffle()`, `queue.clear()`는 다음 트랙들의 순서를 바꿉니다.
- `queue.loop = "track"`은 현재 트랙을, `"queue"`는 큐 전체를 반복하고, `None`은 반복을 멈춥니다. 타입은 `voice.Loop`입니다. 반복에는 `source.again()`을 사용합니다.
- `queue.pause()`, `queue.resume()`, `queue.paused`, `queue.volume`, `queue.filters`는 재생 중인 트랙에 적용됩니다.
- `queue.stop()`은 모든 트랙을 끝냅니다. 나중에 추가한 트랙은 다시 재생됩니다.
- `await queue.join()`은 큐에 남은 트랙도, 재생 중인 트랙도 없을 때까지 기다립니다.

끝난 연결의 큐는 `closed` 상태가 되어 더 이상 트랙을 받지 않습니다. 위 코드처럼 다시 연결한 뒤 새 큐를 만드세요.

## 사운드보드 사운드 { #soundboard-sounds }

`await bot.rest.soundboard.send(vc.channel_id, sound_id=...)`는 봇이 연결된 채널에서 사운드보드 사운드를 재생합니다. 이 사운드는 봇이 보내는 오디오와는 별개로 Discord가 통화에 직접 재생합니다. 다른 서버의 사운드라면 `source_guild_id`를 넘깁니다. `bot.rest.soundboard.list_default()`는 Discord 기본 사운드를, `list(guild_id)`는 서버의 사운드를 나열하며, `bot.request_soundboard_sounds(guild_id)`는 게이트웨이에 사운드 목록을 요청합니다.

## 여러 서버에서 같은 오디오 재생하기 { #the-same-audio-in-several-guilds }

`await voice.broadcast(source, connections)`는 소스 하나를 여러 연결에서 재생하며, 디코딩과 인코딩은 한 번만 합니다. 끝난 연결은 집합에서 빠지고, 나중에 들어온 연결은 추가되지 않습니다. `encoder`와 `stall`을 받으며, `stall`의 기본값은 `"drop"`이라서 멈춘 연결 하나가 다른 연결을 붙잡지 않습니다.

## 인코더 { #encoder }

`voice.EncoderSettings`는 연결의 Opus 인코더를 설정합니다. `Defaults(encoder=...)`, `connect(encoder=...)`, 또는 연결된 동안 `vc.encoder = ...`로 지정합니다.

| 필드 | 기본값 | 의미 |
|---|---|---|
| `bitrate` | `None` | 초당 비트 수, 500~512 000. `None`이면 채널의 비트레이트를 따르고, 없으면 64 000 |
| `application` | `"audio"` | 음악에는 `"audio"`, 음성에는 `"voip"`, 지연을 최소로 하려면 `"lowdelay"` |
| `fec` | `True` | 손실된 패킷을 감추는 순방향 오류 정정 |
| `packet_loss` | 0.1 | `fec`가 대비하는 예상 손실률, 0~1 |
| `complexity` | 10 | 0~10. 낮을수록 CPU를 덜 씁니다 |
| `bandwidth` | `"auto"` | 가장 높은 오디오 대역: `"narrow"`, `"medium"`, `"wide"`, `"superwide"`, `"full"` |
| `signal_type` | `"auto"` | `"voice"` 또는 `"music"`에 맞춰 조정 |

`vc.speaking_flags`는 봇이 알리는 말하기 플래그를 설정합니다. 기본값은 `voice.SpeakingFlags.MICROPHONE`이고, 공유 오디오에는 `SOUNDSHARE`, 우선 발언자에는 `PRIORITY`를 씁니다.
