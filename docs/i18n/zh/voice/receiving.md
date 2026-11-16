---
source: 0d8bbdce2b1aa0ef1ee6009ade35eb117f7d7909
description: "按说话者以 PCM、Opus 或 RTP 包接收 Discord 语音，检测说话、切分语段、中继通话，并把每个用户录制为 Ogg 或 WAV 文件。"
---

# 接收与录音

![Bostie 举着麦克风，声波迎面而来](../assets/mascot/bostie-receiving.webp){ align=right width="180" loading=lazy }

`vc.receive()` 会打开一个接收器：一个有界的异步迭代器，遍历机器人听到的音频。每位说话者的音频单独到达，已在 Rust 中解密、重新排序并解码。一个连接上可以同时打开多个接收器，每个都有自己的格式和滤镜。

```python
--8<-- "docs/snippets/voice/listening.py"
```

## 格式 { #formats }

`vc.receive(format)` 根据一个 `voice.ReceiveFormat` 返回一个 `voice.Receiver`：

| `vc.receive(format)` | 产出项 | 用途 |
|---|---|---|
| `voice.PCM(rate=48000, channels=2, sample="s16le")` | 该格式的已解码采样组成的 `Frame`；采样率为 `voice.DECODE_RATES` 之一，即 8、12、16、24 或 48 kHz | 收听、语音识别、电平 |
| `voice.Opus()` | `data` 为一个 Opus 包的 `Frame` | 无需解码的录音或转发 |
| `voice.Packets()` | 已解密、按到达顺序排列、未经抖动缓冲区的 `Packet` | 你自己的抖动缓冲区、RTP 头部扩展 |

`Frame` 带有 `user_id`、`ssrc`、RTP `timestamp`、基于接收器 48 kHz 时钟的 `offset` 和 `samples`、`received_at`、`data`、`lost`（对于 Opus 为掩盖丢包而生成的音频为真），PCM 还带有 `level`，即该帧以 dBFS 表示的 RMS。时钟从接收器打开时开始计时，因此不同用户的帧可以按 `offset` 对齐。`Packet` 带有 `user_id`、`ssrc`、`sequence`、`timestamp`、`received_at`、`opus` 载荷以及 RTP 头部 `extensions`。

## 选择收听谁 { #choosing-whom-to-hear }

- `users=[...]` 只保留这些用户，`exclude=[...]` 排除某些用户。
- `include_self=True` 加入机器人自己的音频。
- `mix=True`（PCM 和 Opus）传递一条将所有人混在一起的流，`user_id` 设为 `None`。
- `markers=True` 会在帧之间插入 `SpeechStart`、`SpeechStop` 和 `UserLeave` 事件，每个都带有基于同一时钟的 `offset`，因此消费者无需自己计时就能知道说话从哪里开始、到哪里结束。

## 设置 { #settings }

`vc.receive` 接受以下关键字参数；`Packets()` 没有抖动缓冲区，所以不接受 `jitter`。

| 设置 | 默认值 | 含义 |
|---|---|---|
| `buffer` | `Defaults.receive_buffer`，2.0 | 接收器为较慢的消费者保留的产出项秒数，0.02 到 60 |
| `overflow` | `Defaults.overflow`，`"drop_oldest"` | 缓冲区满时：丢弃最早的产出项，或 `"drop_newest"` |
| `jitter` | `Defaults.jitter`，0.1 | 继续播放前等待迟到数据包的秒数，0 到 1；仅限 PCM 和 Opus |
| `stall` | `Defaults.receive_stall`，`"wait"` | 连接重连时：`"wait"` 保持接收器打开，`"end"` 结束它 |

## 读取 { #reading }

`async for item in rx` 会等待产出项，并在接收器结束时停止。`async with rx:` 在代码块结束时关闭它。

- `rx.get_nowait()` 返回下一个产出项，没有排队的产出项时引发 `voice.Empty`，接收器已结束时引发 `voice.ReceiverClosed`。
- `rx.queued` 是最早排队的产出项已等待的时长：即消费者的延迟。
- `rx.clear()` 丢弃所有排队的内容，并继续实时接收。
- `await rx.mark()` 在调用之前接收到的音频都已排队或被丢弃时返回 `True`，或在 1.2 秒加上两倍 jitter 之后返回 `False`；取消某一次等待不会影响其他等待的标记。
- `rx.stats()` 返回一个 `ReceiverStats`：已传递（`delivered`）的产出项、因溢出而丢弃（`dropped`）的产出项，以及途中丢失（`lost`）的数据包。
- `rx.close()` 停止接收。`rx.closed_reason` 是一个 `voice.ClosedReason`，说明接收器结束的原因：`CLOSED`、`DISCONNECTED`、`MOVED`（机器人换了频道）或 `RECONNECTING`（在 `stall="end"` 时）。

版主可以将机器人拒听，此时 Discord 不会向它发送任何音频；在机器人被拒听时打开接收器，或在接收器打开时机器人被拒听，bostie 都会记录一条警告。

## 中继 { #relaying }

接收器和 sink 可以直接相连，把音频从一个通话传到另一个通话：

```python
--8<-- "docs/snippets/voice/relay.py"
```

混合帧没有 `user_id`、`ssrc` 或 `timestamp`，混音流也无法携带标记。丢失的 Opus 帧的 `data` 为空，因为没有可以传递的数据包；在写入 Opus sink 之前请跳过它，就像 `relay_speaker` 那样。两端默认都是 48 kHz 立体声 16 位 PCM，因此 PCM 接收器可以直接写入默认的 sink。

## 说话检测 { #speech }

只要 `speech_activity` 开启，每个连接上都会在 Rust 中运行说话检测：用户的第一段音频即表示开始说话，静默 `speech_hangover` 秒（默认 0.2）后视为停止。`vc.speaking` 保存当前正在说话的人，`SpeechStart` 和 `SpeechStop` 事件把它报告给监听器，以 `markers=True` 打开的接收器则把它带在自己的流中。当 `speech_activity` 关闭且没有打开的接收器时，bostie 甚至不会解密收到的音频。

`voice.utterances(rx, min_silence=0.8, max_length=30.0)` 把 PCM 接收器的帧分组为 `Utterance`：一位用户从开始到结束的一段话，带有接收器格式的 `data`、其 `offset`、`duration` 以及丢失（`lost`）的秒数。语段在没有说话持续 `min_silence` 秒后，或达到 `max_length` 秒时结束。它需要一个不带 `mix`、以 `markers=True` 打开的 PCM 接收器，或者一个以 dBFS 表示的 `level`，此时由它代替标记来决定什么算作说话。

## 录音 { #recording }

```python
--8<-- "docs/snippets/voice/call_recording.py"
```

`voice.Recorder(vc, path)` 把每位用户录制到各自的文件中，或者在 `mix=True` 时把整个通话录制到一个文件中。路径是一个包含 `{guild_id}` 和 `{user_id}` 的模板，每位用户一个文件时 `{user_id}` 是必需的，`mix=True` 时则不允许使用，路径的扩展名决定容器格式：

- `.ogg` 或 `.opus` 写入 Ogg Opus。按用户录制时，收到的 Opus 包会原样写入，无需解码。
- `.wav` 以录音器的 `format` 写入 16 位 PCM，默认为 48 kHz 立体声；其采样率为 `voice.DECODE_RATES` 之一。

默认的 `align=True` 会用静音填补空隙，因此每个文件都从录音开始时开始，各文件可以对齐。`users`、`exclude` 和 `include_self` 选择录制谁，与接收器相同；`buffer`（30 秒）和 `jitter`（0.3 秒）比接收器的默认值更大，因为磁盘可能会卡顿。录音会在 `max_duration` 秒后、文件达到 `max_size` 字节后，或连接结束时自行结束。

`await recorder.stop()` 会在已听到的音频写入完毕后结束录音，并以从用户 ID 到 `Path` 的字典形式返回文件，混音文件的键为 `None`。`await recorder.wait()` 等待它自行结束。`recorder.files` 列出到目前为止写入的文件，`recorder.stats()` 返回接收器的计数器，其中 `dropped` 表示磁盘落后的时间超过了 `buffer`。

请告诉通话中的人机器人正在录音，就像示例代码那样。
