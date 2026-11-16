---
source: d85e30b6bb45eff282f70f86375dc49e252a5387
description: "在 Discord 语音中播放文件、URL 和 HLS 流：音轨、音量、均衡器与变速滤镜、支持交叉淡入淡出的无缝队列以及 Opus 编码器设置。"
---

# 播放音频

![Bostie 随着音乐点头](../assets/mascot/bostie-playback.webp){ align=right width="180" loading=lazy }

`await vc.play(source)` 在一个[语音连接](index.md)上播放一个音源，并立即返回一个 `Track`。多个音轨可以同时播放并被混音；`Queue` 则让它们依次播放。

```python
--8<-- "docs/snippets/voice/tracks.py"
```

## 音源 { #sources }

音源就是要播放的音频。每个音源只能使用一次：`play` 会打开它，在它结束后再次打开会引发 `StateError`，`source.again()` 返回一个音频相同的新音源，用于再播放一次。

| 音源 | 播放内容 | 可跳转 |
|---|---|---|
| `voice.File(path)` | 本地文件 | 是 |
| `voice.Bytes(data, hint=None)` | 内存中的文件内容；`hint` 是扩展名，例如 `"ogg"` | 是 |
| `voice.Url(url, headers=None)` | 直接的 HTTP 或 HTTPS 媒体 URL（流式播放），或 HLS 播放列表 | 服务器接受范围请求时可以；HLS 不可以 |
| `voice.FFmpeg(args, executable="ffmpeg")` | ffmpeg 命令的输出，用于 bostie 不能解码的格式 | 否 |
| `voice.Stream(items, format)` | 由 `format` 格式的 PCM 数据块或 Opus 包组成的异步可迭代对象 | 否 |

`File`、`Bytes` 和 `Url` 在 Rust 中解码：Ogg（Opus、Vorbis）、WebM 和 Matroska、MP4 和 M4A（AAC）、MP3、FLAC 以及 WAV。URL 必须指向媒体本身，而不是嵌入了媒体的网页。与服务器的连接中断时，`Url` 最多重连三次，分别在 0.5、1 和 2 秒后，如果服务器接受范围请求，就从中断处继续。HLS 主播放列表会播放其中的音频版本，直播播放列表从末尾往前三个分段处开始播放，加密的播放列表和字节范围分段会以 `SourceError` 拒绝。`FFmpeg` 用你给出的输入参数运行该程序，并从其输出读取 48 kHz 立体声 16 位 PCM；当程序不在 `PATH` 中或以错误退出时，引发 `SourceError`。无法打开、解码或跳转的音源会引发 `SourceError`，或以原因 `ERROR` 结束其音轨。

在 `open()`（它会设置 `opened`）之后，音源带有 `format`、`duration`（秒，未知时为 `None`）、`seekable` 和 `tags`，后者是一个 `voice.Tags`，包含文件中写明的 `title`、`artist` 和 `album`。

```python
--8<-- "docs/snippets/voice/other_sources.py"
```

48 kHz 立体声、20 ms 一包的 Opus 音频，例如大多数 Ogg Opus 和 WebM 文件，在单独播放、音量为 1 且没有滤镜时会原样发送，无需重新解码和编码。`track.passthrough` 会告诉你何时发生这种情况。

## 音轨 { #tracks }

`vc.play(source, volume=1.0, filters=Filters(), meta=None)` 接受初始音量和滤镜，以及 `meta`，即任何要与该音轨一起保存的值，例如是谁点的歌。它还接受 `buffer` 和 `stall`，作用于该音轨的 [sink](sinks.md)。

| `Track` | 作用 |
|---|---|
| `await track.wait()` | 等待结束并返回一个 `TrackEnd`；取消等待不会停止音轨 |
| `await track.started()` | 等待它开始播放；如果它先结束了则为 `False` |
| `track.ended` | 该 `TrackEnd`，播放期间为 `None` |
| `track.position` | 音源目前已播放的秒数 |
| `track.pause()`、`track.resume()`、`track.paused` | 停止和恢复向它供给数据；已缓冲的音频仍会播放 |
| `await track.seek(seconds)` | 从某个位置继续；音源不能跳转时引发 `SourceError` |
| `track.volume`、`track.filters` | 播放期间修改后立即生效 |
| `track.stop()` | 结束它 |
| `track.source`、`track.meta`、`track.sink` | 它播放的内容、你保存的值，以及它写入的 sink |

`TrackEnd` 带有 `track`、`reason`（`FINISHED`、`STOPPED`、带有 `error` 的 `ERROR`，或连接结束时的 `CLEANUP`）以及 `may_start_next`，后者在 `FINISHED` 或 `ERROR` 之后为真，表示你自己的播放器是否应该继续播放下一个音轨。`vc.stop()` 会结束所有音轨。

## 音量与滤镜 { #volume-and-filters }

`volume` 与采样值相乘：0.5 表示一半的音量，1.0 保持不变。`voice.Filters` 设置 Rust 在播放音频时应用的效果：

| `Filters` 字段 | 默认值 | 范围 |
|---|---|---|
| `equalizer` | `{}` | 最多 16 个频段，把 20 到 20 000 Hz 的中心频率映射到以 dB 为单位的增益；每个频段宽一个倍频程 |
| `bass` | 0.0 | 100 Hz 以下的增益，单位 dB |
| `speed` | 1.0 | 0.5 到 2.0，保持音高不变 |
| `pitch` | 1.0 | 0.5 到 2.0，保持节奏不变 |

增益范围为 -24 到 24 dB，超出范围的值会引发 `ValueError`。

## 队列 { #queues }

```python
--8<-- "docs/snippets/voice/queue_player.py"
```

`voice.Queue(vc)` 无缝地一首接一首播放音轨，或以 `crossfade` 秒的重叠交叉淡入淡出。它接受 `volume`、`filters`、`crossfade` 和 `history`，这些默认取连接的设置，还接受 sink 的 `buffer` 和 `stall`。

- `queue.add(source, meta=None, index=None)` 把一个音源加到队尾，或加到即将播放的音轨中的 `index` 位置，并返回其 `Track`。
- `queue.now`、`queue.upcoming` 和 `queue.history` 分别是当前音轨、接下来的音轨，以及最多 `history` 个已播放的音轨。
- `queue.skip()` 结束当前音轨；`queue.back()` 再次播放历史中的最后一个音轨，然后重新播放当前音轨。
- `queue.move(track, index)`、`queue.remove(track)`、`queue.shuffle()` 和 `queue.clear()` 用于重新排列即将播放的音轨。
- `queue.loop = "track"` 循环当前音轨，`"queue"` 循环整个队列，`None` 停止循环；类型为 `voice.Loop`。循环使用 `source.again()`。
- `queue.pause()`、`queue.resume()`、`queue.paused`、`queue.volume` 和 `queue.filters` 作用于正在播放的内容。
- `queue.stop()` 结束所有音轨；之后添加的音轨会再次播放。
- `await queue.join()` 等待直到队列中没有任何音轨在排队或播放。

已结束连接上的队列处于 `closed` 状态，不再接受新音轨；重连之后请新建一个队列，就像示例代码那样。

## 音效板音效 { #soundboard-sounds }

`await bot.rest.soundboard.send(vc.channel_id, sound_id=...)` 会在机器人连接的频道中播放一个音效板音效。Discord 会直接在通话中播放它，与机器人发送的任何内容并行；对于来自其他服务器的音效，请传入 `source_guild_id`。`bot.rest.soundboard.list_default()` 列出 Discord 自带的音效，`list(guild_id)` 列出某个服务器的音效，`bot.request_soundboard_sounds(guild_id)` 则通过网关请求它们。

## 在多个服务器中播放同一段音频 { #the-same-audio-in-several-guilds }

`await voice.broadcast(source, connections)` 在多个连接上播放同一个音源，只解码和编码一次。结束的连接会离开该集合；之后加入的连接不会被添加进来。它接受 `encoder` 和 `stall`，后者默认为 `"drop"`，这样一个停滞的连接不会拖累其他连接。

## 编码器 { #encoder }

`voice.EncoderSettings` 配置连接的 Opus 编码器，可以通过 `Defaults(encoder=...)`、`connect(encoder=...)` 设置，或在连接期间通过 `vc.encoder = ...` 设置：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `bitrate` | `None` | 每秒比特数，500 到 512 000；`None` 表示跟随频道的比特率，或 64 000 |
| `application` | `"audio"` | `"audio"` 适合音乐，`"voip"` 适合语音，`"lowdelay"` 延迟最低 |
| `fec` | `True` | 前向纠错，可以掩盖丢失的数据包 |
| `packet_loss` | 0.1 | `fec` 所针对的预期丢包率，0 到 1 |
| `complexity` | 10 | 0 到 10；越低占用的 CPU 越少 |
| `bandwidth` | `"auto"` | 最高音频带宽：`"narrow"`、`"medium"`、`"wide"`、`"superwide"` 或 `"full"` |
| `signal_type` | `"auto"` | 针对 `"voice"` 或 `"music"` 进行调优 |

`vc.speaking_flags` 设置机器人宣告的说话标志，默认为 `voice.SpeakingFlags.MICROPHONE`，`SOUNDSHARE` 用于共享的音频，`PRIORITY` 用于优先发言人。
