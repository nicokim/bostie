---
source: 5246991ba1478ea5d33d14802d1a46c835ecb91e
---

# 语音连接

![Bostie 戴着耳机聆听](../assets/mascot/bostie-listen.webp){ align=right width="180" loading=lazy }

bostie 的语音引擎运行在它的 Rust 核心中：语音 WebSocket、UDP 传输、20 ms 发送时钟、解码与重采样、混音器、Opus 编解码、传输加密，以及 DAVE 端到端加密。Python 只负责让它加入、播放和接收；时序从不依赖事件循环，唯一的例外是 Python 需要保持每个 sink 的缓冲区有数据。

语音需要 `Intents.GUILD_VOICE_STATES`，`Intents.DEFAULT` 中已包含它。机器人在每个服务器中最多有一个语音连接。

```python
--8<-- "docs/snippets/voice/voice_connection.py"
```

## 连接 { #connecting }

`await ctx.voice.connect()` 会加入执行命令的用户所在的语音频道或舞台频道，并在音频可以双向流动时返回。它会在缓存中查找用户所在的频道，如果该服务器没有被缓存，则向 Discord 查询；当用户不在该服务器的任何语音频道中，或交互来自私信时，引发 `NotInVoice`。`ctx.voice.current` 是机器人在该服务器中的连接，或 `None`。

在处理函数之外，`await bot.voice.connect(channel_id)` 按 ID 加入频道。在机器人已经连接的服务器中，它会移动连接，并只应用所给出的选项。`bot.voice.get(guild_id)` 和 `bot.voice.connections` 返回打开的连接，`await bot.voice.close()` 会像 `bot.close()` 那样离开所有连接。

如果加入在 `timeout` 秒内没有就绪，会引发原因为 `TIMEOUT` 的 `VoiceConnectError`；对于加入已满的频道或机器人无权连接的频道，Discord 会不作回应。其他失败会引发带有原因、关闭码和 Discord 消息的 `VoiceConnectError`。

## 设置 { #settings }

`Bot(voice=voice.Defaults(...))` 设置每个连接的初始值，`connect()` 则为单次调用接受连接设置：

| `voice.Defaults` 字段 | 默认值 | 含义 |
|---|---|---|
| `self_mute`、`self_deaf` | `False` | 机器人自己的静音和拒听 |
| `timeout` | 10.0 | 等待加入、移动或编辑的秒数 |
| `server_change_delay` | 0.0 | 跟随 Discord 切换到新语音服务器之前等待的秒数 |
| `encoder` | `EncoderSettings()` | Opus 编码器；参见[播放音频](playback.md#encoder) |
| `speaking_flags` | `MICROPHONE` | 机器人播放时宣告的 `voice.SpeakingFlags` |
| `stage` | `"listen"` | 在舞台上：`"listen"`，`"request"` 请求发言，或 `"speak"` |
| `speech_hangover` | 0.2 | 静默多少秒后视为说话者停止说话，0.02 到 10 |
| `speech_activity` | `True` | 是否触发开始说话和停止说话事件 |
| `stall_after` | 2.0 | 媒体停止流动多少秒后由 `MediaStalled` 报告，至少 0.02 |
| `sink_buffer`、`stall` | 0.2、`"hold"` | 每个 [sink](sinks.md) 的默认值 |
| `receive_buffer`、`jitter`、`overflow`、`receive_stall` | 2.0、0.1、`"drop_oldest"`、`"wait"` | 每个[接收器](receiving.md)的默认值 |
| `crossfade`、`history` | 0.0、0 | 每个[队列](playback.md#queues)的默认值 |

`connect()` 以关键字参数接受 `self_mute`、`self_deaf`、`timeout`、`server_change_delay`、`encoder`、`speaking_flags`、`stage`、`speech_hangover`、`speech_activity` 和 `stall_after`；省略或传入 `None` 的保持默认值。`connect_external()` 接受前三个，`move()` 接受 `timeout`，`edit()` 接受 `self_mute`、`self_deaf` 和 `timeout`。无效的值会在构建 `Defaults` 时引发 `ValueError`。字符串类型的设置使用字面量类型 `voice.StageRole`、`voice.SinkStall`、`voice.ReceiveStall`、`voice.Overflow`、`voice.Sample`、`voice.OpusApplication`、`voice.OpusBandwidth` 和 `voice.OpusSignal`。`ctx.voice` 是一个 `voice.ContextVoice`，`bot.voice` 是一个 `voice.VoiceManager`。

## 连接对象 { #the-connection }

| `VoiceConnection` | 值 |
|---|---|
| `guild_id`、`channel_id` | 连接所在的位置 |
| `state` | 一个 `voice.State`：`CONNECTING`、`READY`、`RESUMING`、`RECONNECTING` 或 `DISCONNECTED` |
| `members` | 通话中其他用户的 ID |
| `speaking` | 当前正在说话的用户，映射到他们开始说话时的 `time.monotonic()` |
| `ssrcs` | 通话中每个 SSRC 映射到其用户 ID |
| `self_mute`、`self_deaf` | 机器人自己的静音和拒听，以 Discord 实际应用的为准 |
| `mute`、`deaf`、`suppress` | 版主是否将机器人静音或拒听，或者它在这个舞台上是否不能发言 |
| `latency` | 一个 `Latency`，包含 WebSocket 和 UDP 的往返时间（秒），测得之前为 `None` |
| `dave` | 通话的端到端加密；见下文 |
| `sinks` | 打开的 sink，最早打开的在前 |
| `encoder`、`speaking_flags`、`speech_hangover`、`speech_activity`、`stall_after` | 连接期间可以修改 |

- `await vc.move(channel_id)` 移动到该服务器的另一个语音频道或舞台频道，并在媒体在那里开始流动后返回。
- `await vc.edit(self_mute=..., self_deaf=...)` 修改机器人自己的静音或拒听，未给出的那一项保持不变，并在 Discord 应用后返回。
- `await vc.stage("request")` 在舞台上请求发言，`"speak"` 开始发言（需要相应权限），`"listen"` 回到听众席。
- `await vc.wait_until(voice.State.READY)` 等待某个状态，如果连接先结束了，则引发 `VoiceNotConnected`。
- `vc.reconnect()` 断开语音 WebSocket，并在新的连接上恢复。
- `vc.stop()` 关闭所有 sink，于是不再播放任何内容。
- `await vc.disconnect()` 离开频道，并在连接结束后返回，最多 2 秒。

在已结束的连接上调用方法会引发 `VoiceNotConnected`。

连接会自我修复。断开的 WebSocket 会恢复，丢失的会话会重连，当版主移动机器人时，它会跟随到新频道并发出一个 `Moved` 事件。当版主断开它的连接或删除频道时，它会以原因 `REMOVED` 结束。当机器人在被服务器静音或在舞台上被禁止发言时播放，以及在它被拒听时接收器仍在等待，bostie 都会发出警告。

## 端到端加密 { #end-to-end-encryption }

bostie 支持 DAVE，即 Discord 的语音端到端加密，无需任何设置：它会加入通话的加密组，加密它发送的内容，解密它接收的内容。`vc.dave` 以 `voice.Dave` 的形式报告其状态：

- `protocol_version` 是正在使用的 DAVE 协议版本，0 表示未使用；
- `epoch` 记录加密组成员的变更次数；
- `privacy_code` 是 Discord 客户端为该通话显示的代码；
- `await vc.dave.verification_code(user_id)` 返回用于与另一位用户在通话之外核对的代码，以确认中间没有其他人。

每次变化都会由一个 `DaveTransition` 事件报告。要求 DAVE 的频道遇到不支持 DAVE 的客户端时，会以原因 `DAVE_REQUIRED` 关闭，DAVE 的某个步骤失败时会引发 `DaveError`，或以 `DAVE_FAILED` 结束连接。

## 事件 { #events }

语音连接通过 `@bot.listen` 发送其事件。每个事件都是带有 `guild_id` 的 `voice.VoiceEvent`。进入 `DISCONNECTED` 的 `StateChange` 带有一个 `voice.DisconnectReason`：`REQUESTED`（调用了 `disconnect()`）、`REMOVED`（版主断开了机器人的连接或删除了频道）、`CALL_TERMINATED`、`RATE_LIMITED`、`TIMEOUT`、`SESSION_INVALID`、`AUTHENTICATION_FAILED`、`DAVE_REQUIRED`、`DAVE_FAILED`、`PROTOCOL_ERROR` 或 `CLOSED`（机器人已关闭）。

| 事件 | 字段 | 何时发出 |
|---|---|---|
| `StateChange` | `old`、`new`、`reason`、`code`、`detail` | 连接状态发生变化 |
| `Moved` | `old_channel_id`、`new_channel_id`、`by_us` | 机器人被移动，由 `move()` 或版主触发 |
| `DaveTransition` | `protocol_version`、`epoch`、`transition_id` | 端到端加密发生变化 |
| `MediaStalled`、`MediaResumed` | `direction`（`voice.Direction.SEND` 或 `RECEIVE`）、`duration` | 发送或接收停止，或重新开始 |
| `UserJoin`、`UserLeave` | `user_id` | 有用户加入或离开通话 |
| `SpeakingUpdate` | `user_id`、`ssrc`、`flags` | 有用户宣告了其 SSRC 和说话标志 |
| `SpeechStart`、`SpeechStop` | `user_id`、`ssrc`、`at`，停止时还有 `duration` | 有用户开始或停止说话 |
| `RtcpPacket` | `packet_type`、`count`、`ssrc`、`user_id`、`reports`、`data` | 语音服务器发送了一个 RTCP 包；`reports` 中的每个 `ReceptionReport` 都包含某条流的 `fraction_lost`、`lost`、`highest_sequence`、`jitter`、`last_sender_report` 和 `delay` |

它们位于 `bostie.voice` 中。`SpeechStart` 和 `SpeechStop` 只在 `speech_activity` 开启时触发。

## 媒体停滞 { #stalled-media }

```python
--8<-- "docs/snippets/voice/media_stalls.py"
```

`direction` 为 `SEND` 的 `MediaStalled` 会在音频在 sink 中等待、却在 `stall_after` 秒内无法发出时触发，例如在重连或 DAVE 过渡期间。为 `RECEIVE` 时，表示数据包到达了一个打开的接收器，但在这段时间内没有一个能被解密或播放。媒体重新开始流动后会接着发出 `MediaResumed`，其 `duration` 从停滞开始时计算。`stall_after` 为 2 秒，除非 `voice.Defaults`、`connect(stall_after=...)` 或 `vc.stall_after` 另有设置。

## 离开空频道 { #leaving-an-empty-channel }

机器人会一直留在频道中，直到断开连接。要在最后一位用户离开时离开：

```python
--8<-- "docs/snippets/voice/leave_when_alone.py"
```

## 错误 { #errors }

语音错误都是 `VoiceError`：`VoiceConnectError`（加入在就绪之前就结束了；带有 `reason`、`code`、`detail`）、`NotInVoice`、`VoiceNotConnected`、`AlreadyConnected`、`VoiceQueueFull`（等待该连接的命令过多）、`DaveError`，以及音频相关的 `SourceError`、`SinkClosed`、`BufferFull`、`Empty` 和 `ReceiverClosed`，后续页面会解释它们。

## 外部语音引擎 { #external-voice-engines }

```python
--8<-- "docs/snippets/voice/external_engine.py"
```

`await bot.voice.connect_external(guild_id, channel_id)` 为一个在别处运行的语音引擎加入频道，并返回一个 `ExternalVoice`，其中带有引擎连接所需的 `state`（`VoiceStateUpdate`）和 `server`（`VoiceServerUpdate`）。之后 bostie 不会在该服务器中发送音频。`async with external.updates() as updates:` 会传递该服务器之后的语音服务器信息以及机器人自己的语音状态，`await external.disconnect()` 会离开频道。在 bostie 自己维护连接的服务器中，它会引发 `AlreadyConnected`；它接受 `self_mute`、`self_deaf` 和 `timeout`。

## 版本 { #versions }

`voice.versions()` 返回编译进当前构建的 bostie、libopus、DAVE 库的版本，以及所支持的最高 DAVE 协议版本。
