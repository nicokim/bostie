---
source: 0d5db0165c75f4671bf7c09cfef1394b9bfa9188
description: "无需 Discord 即可测试机器人：在本地模拟的网关、REST API 和语音服务器上运行它，发送交互和事件，并检查它发送了什么。"
---

# 测试你的机器人

![Bostie 拿着放大镜搜寻，直到出现一个对勾](../assets/mascot/bostie-detective.webp){ align=right width="180" loading=lazy }

`bostie.testing` 会在 localhost 上运行一个模拟 Discord：一个网关、一个包含机器人最常用路由的 REST API，以及可选的语音服务器。你真实的机器人连接到它，测试则扮演 Discord 一方：发送事件和交互，然后检查机器人发回了什么。它需要 `testing` 附加依赖（[安装](../install.md)），bostie 自己的测试套件就运行在它之上。

```python
--8<-- "docs/snippets/testing/test_greeter.py"
```

`run(scenario)` 会启动一个 `FakeDiscord`，在 10 秒内对它运行 `async def scenario(fake)`，然后将其关闭；它是一个普通函数，所以 pytest 不需要插件。`async with running(bot, fake):` 在模拟环境上启动机器人，等待它就绪，并在结束时关闭它。用 `Bot(api_url=fake.api_url)` 让机器人指向模拟环境，如果机器人是在别处构建的，则设置 `BOSTIE_API_URL`。

## 扮演 Discord 一方 { #driving-discords-side }

| 调用 | 作用 |
|---|---|
| `await fake.interaction(type, data)` | 发送一个 `INTERACTION_CREATE`，并以 `Recorded` 请求的形式返回机器人的第一次回应 |
| `await fake.dispatch(name, d, shard=0)` | 发送任意网关事件，并返回其序列号 |
| `fake.expect_callback(payload)` | 用于你自行分发的交互：返回一个等待其第一次回应的 future |
| `await fake.send_op(op, d)` | 发送一个非 dispatch 的操作码，例如 7（重连）或 9（会话无效） |
| `await fake.close_gateway(code)`、`fake.drop_gateway()` | 以某个关闭码关闭连接，或不带关闭码直接断开 |
| `fake.hold_acks()` | 停止回应心跳，就像连接已死一样 |
| `await fake.next_identify()`、`await fake.next_resume()` | 等待机器人的下一次 IDENTIFY 或 RESUME |

交互类型使用 Discord 的编号：2 表示命令，3 表示组件，4 表示自动补全，5 表示模态框提交。

在会开始新会话的关闭（例如 4007）之后，机器人要等 `HTTPSettings.identify_spacing` 秒才会再次 identify，默认值为 Discord 要求的 5 秒。在测试中，`Bot(http_settings=HTTPSettings(identify_spacing=0))` 可以跳过这段等待。

`bostie.testing.payloads` 根据 Discord 文档中的示例构建载荷，并使用全新的 ID：

- `command(name, *options)`、`option(name, type, value, focused=False)`、`subcommand(...)`、`group(...)` 和 `resolved()` 用于命令和自动补全；
- `context_menu(type, name, target_id, resolved)` 用于用户命令和消息命令；
- `button(custom_id)`、`select(custom_id, *values)` 和 `modal(custom_id)` 用于组件和模态框；
- `message(...)`、`guild(...)`、`member(...)`、`user(...)`、`role(...)`、`channel(...)`、`voice_channel(...)`、`stage_channel(...)` 和 `voice_state(...)` 用于事件，每个都接受用来覆盖字段的关键字参数；
- `fixture(name)` 用于 bostie 附带的任何文档化载荷，`snowflake()` 用于生成新 ID。

## 检查机器人发送了什么 { #checking-what-the-bot-sent }

`fake.requests` 以 `Recorded` 的形式列出每个 REST 请求，`fake.expect_request(method, route_prefix)` 返回一个等待下一个匹配请求的 future，适用于机器人自己发起的请求，例如来自任务的请求。`Recorded` 带有 `method`、`path`（相对于 API 基础地址）、`query`、`headers`、`json`，上传时还有 `parts`。作为交互回应的 `Recorded`，其回调请求体在 `json` 中，就像上面那样。`fake.gateway_received` 是机器人在网关上发送的载荷组成的队列，`fake.connections` 是各条网关连接，即带有其 `received` 载荷、`shard` 和 `close_code` 的 `Connection` 对象。

`fake.cdn_file(path, data)` 会在它返回的一个 CDN URL 上提供 `data`，用于机器人要下载的附件或素材，`fake.cdn_requests` 记录这些下载。

模拟环境会保存这些路由所需的状态，测试可以读取或预先填充这些状态：`fake.guilds`、`fake.channels`、`fake.users`、`fake.members`、`fake.messages`、`fake.commands` 和 `fake.guild_commands`，以及用于命令同步的 `fake.put_count`。`fake.ready_guilds` 设置 `READY` 中列出的服务器 ID。

## 故障与时序 { #failures-and-timing }

- `fake.fail(method, route_prefix, status, json)` 以一个错误回应下一个匹配的请求，也可以为模拟环境不提供的路由打桩。
- `fake.rate_limit(method, route_prefix, scope="user", retry_after=0.1)` 以 429 回应它。
- `fake.answer(method, route_prefix, body, content_type)` 以一个非 JSON 的请求体回应它。
- `fake.hold(method, route_prefix)` 让它一直等待，直到你设置返回的 `asyncio.Event`；`fake.hold_identify(shard)` 和 `fake.hold_hello()` 对网关握手做同样的事。
- `await settle()` 会让出事件循环若干次，使你刚才的操作所调度的回调得以运行。

要等待机器人在监听器中做的某件事，请让它[分发一个自定义事件](events.md#events-of-your-own)并等待该事件，就像第二个测试那样，而不要用 sleep。

`FakeDiscord(compress=True)` 会用 zstd 压缩网关，这需要 Python 3.14 的 `compression.zstd`；`heartbeat_interval` 设置它要求的心跳间隔，默认是 Discord 的 41 250 ms。

## 语音 { #voice }

`FakeDiscord(voice=True)` 会添加一个语音频道和一个舞台频道，回应语音状态更新，并以 `fake.voice` 运行一个语音网关和 UDP 服务器，因此 `ctx.voice.connect()` 和 `vc.play(...)` 都可以对它运行。它不支持 DAVE；这部分由 bostie 的 Rust 测试覆盖。测试可以：

- 通过 `await fake.next_voice_state_update()`、`await fake.voice.next_identify()` 和 `await fake.voice.wait_for_packets(count)` 等待机器人发送的内容，最后一个会以 `Packet` 对象的形式返回解密后的 RTP 包；每个语音连接都是 `fake.voice.sessions` 中的一个 `VoiceSession`，位于一个 `VoiceSocket` 上，`fake.voice.op(op, d)` 会向最新的连接发送任意语音操作码；
- 通过 `fake.voice.clients_connect(user_ids)`、`fake.voice.client_disconnect(user_id)`、`fake.voice.speaking(user_id, ssrc, flags)`、`fake.voice.send_audio(...)`（向机器人发送 Opus 包）、`fake.voice.send_rtcp(packet)`（向它发送一个 RTCP 包）以及 `fake.voice.send_datagram(data)`（不加密地发送字节，就像出故障的服务器那样）扮演其他用户；
- 用 `fake.move_bot(channel_id)` 和 `fake.kick_bot()`，像版主那样移动机器人或断开其连接；
- 用 `fake.hold_voice_state()` 让下一次语音状态更新得不到回应，就像频道已满时 Discord 的做法一样，直到返回的 `asyncio.Event` 被设置；
- 设置 `fake.mute`、`fake.deaf` 和 `fake.suppress`，作用于它发送的关于机器人的语音状态，设置 `voice_order`，控制 Discord 在加入时发送的两个事件的顺序。
