---
source: 7da84385c00fe71bc6575b90de4a0a059883399e
---

# Sink 与原始音频

sink 是一个有界缓冲区，Python 向其中写入音频，Rust 混音器每次从中读取 20 ms。每个音轨和队列都通过一个 sink 播放。对于你自己生成或从其他地方接收的音频，可以打开自己的 sink：合成器、文字转语音引擎、来自另一个程序的音频流。

```python
--8<-- "docs/snippets/voice/tone_sink.py"
```

## 格式 { #formats }

`vc.sink(format)` 根据你将要写入的内容（一个 `voice.Format`）返回一个 `voice.Sink`：

- `voice.PCM(rate=48000, channels=2, sample="s16le")`：交错的小端序采样，`"s16le"`（16 位整数）或 `"f32le"`（32 位浮点数），单声道或立体声，采样率 8000 到 192 000 Hz。bostie 在 Rust 中重采样到 48 kHz；采样率必须能以每块最多 960 个采样的方式整除到 48 kHz，所有常见采样率都满足这一点。
- `voice.Opus()`：每次写入一个 Opus 包。当 sink 单独播放、音量为 1 且没有滤镜时，48 kHz 立体声、20 ms 的包会原样发送；其他情况则会被解码并混音。

`voice.OpusEncoder(pcm_format, encoder=EncoderSettings())` 在 Python 进程中把 PCM 编码成这样的包，适用于只想编码一次、然后发送到多个 sink 的音频：`encode(data)` 返回到目前为止所有完整的包，`flush()` 用静音补齐剩余部分。它的比特率默认为 64 kbps，因为它没有可以跟随的频道。

## 写入 { #writing }

- `await sink.write(data)` 把音频排入队列，当 sink 中已有 `buffer` 秒的音频时会等待。这种等待就是让你的生产者与实时保持同步的背压。
- `sink.write_nowait(data)` 一次性把整个数据块排入队列，否则引发 `BufferFull`。
- `async with sink:` 在代码块结束时等待所有已写入的内容播放完毕，然后关闭 sink；如果代码块中出现异常，则改为丢弃排队中的内容。
- `await sink.drain()` 等待到目前为止写入的所有内容播放完毕；如果其中有部分被丢弃则为 `False`。
- `sink.clear()` 丢弃排队中的内容；`clear(until=marker)` 只丢弃标记之前的部分，`clear(since=marker)` 只丢弃标记之后的部分。
- `sink.close()` 丢弃排队中的内容并关闭。向已关闭的 sink 写入会引发 `SinkClosed`。

`sink.mark()` 返回一个位于目前已写入音频之后的 `Marker`。`await marker` 在标记之前的所有音频都播放完毕时返回 `True`，如果其中有部分先被丢弃了则返回 `False`；`marker.done` 表示结果是否已经确定。播放器正是靠标记来判断一个音轨何时真正结束，而不仅仅是何时写完。

## 设置 { #settings }

`vc.sink` 接受以下关键字参数；`vc.play` 和 `voice.Queue` 也接受 `buffer` 和 `stall`。

| 设置 | 默认值 | 含义 |
|---|---|---|
| `buffer` | `Defaults.sink_buffer`，0.2 | sink 在 `write` 开始等待之前所容纳的秒数，0.02 到 60 |
| `stall` | `Defaults.stall`，`"hold"` | 音频无法发送时（例如重连或 DAVE 过渡期间）的处理方式：`"hold"` 保留音频以便之后播放，`"drop"` 实时丢弃，适用于不能落后的直播音频 |
| `volume` | 1.0 | 与采样值相乘；播放期间可通过 `sink.volume` 修改 |
| `filters` | `Filters()` | [效果](playback.md#volume-and-filters)；播放期间可通过 `sink.filters` 修改 |
| `after` | `None` | 同一连接上的另一个 sink；在那个 sink 关闭，或播完其最后一次 `drain()` 之前写入的内容之前，这个 sink 保持静默 |
| `crossfade` | 0.0 | 与 `after` 搭配使用，表示提前开始并交叉淡入淡出的秒数；sink 会因此多缓冲这么多的音频 |

`after` 和 `crossfade` 是队列实现无缝播放的方式：下一个音轨的 sink 在当前音轨播放时就开始填充，并在精确的采样点接手。

## 状态与计数器 { #state-and-counters }

`sink.queued` 是等待中的音频秒数，`sink.passthrough` 表示上一个包是否按写入的原样发出，`sink.closed` 表示它是否已关闭。`sink.stats()` 返回一个 `SinkStats`：

| 字段 | 值 |
|---|---|
| `sent` | 已发送的音频秒数 |
| `position` | 播放头位置（秒），与标记的计数方式一致 |
| `underruns` | sink 在播放期间（`drain()` 之外）数据耗尽的次数：生产者跟不上了 |
| `held_ticks` | 由于音频无法发送而使 sink 被暂停的时钟周期数 |
| `dropped` | 因 `"drop"` 停滞策略而丢弃的秒数 |
| `passthrough_packets` | 未经解码、按写入原样发送的包数 |

欠载次数是最值得关注的数字：无法保持领先 `buffer` 秒的生产者要么太慢，要么事件循环被阻塞了。`vc.sinks` 列出一个连接上打开的 sink。
