---
render_macros: true
source: a3a1b83398a63bd2c49a018f148793d0781c22a0
---

<div class="bostie-hero" markdown>
<div class="bostie-hero__copy" markdown>

# bostie

bostie 是一个 Python 的 Discord 库，它的网关、HTTP 客户端和语音引擎都运行在 Rust 中。你用带类型注解的异步 Python 编写机器人。

</div>

![Bostie，一只波士顿梗，张开双臂欢迎你](assets/mascot/bostie-welcoming.webp){ .bostie-hero__mascot width="200" }

</div>

!!! warning "Pre-alpha 阶段"
    bostie 的 API 仍可能变化。斜杠命令、用户命令和消息命令、组件、模态框、网关事件、缓存、REST、Webhook、HTTP 交互、OAuth2，以及语音的播放与接收，目前都已可用。

```python
--8<-- "docs/snippets/home/readme_example.py"
```

`/play` 会加入执行该命令的用户所在的语音频道，以流式方式播放 URL，并回应一个 Stop 按钮。按钮的自定义 ID 中带有服务器 ID，`@bot.on_component` 会把它解析回 `int`，因此机器人重启后按钮依然有效。`bot.run()` 从 `BOSTIE_TOKEN` 环境变量读取令牌，并在命令有变化时把它发送给 Discord。

{% include "callout.md" %}

## 它为什么而生 { #what-it-is-built-for }

### 语音 { #voice }

自 2026 年 3 月 1 日起，Discord 只接受端到端加密的通话（[DAVE](https://docs.discord.com/developers/topics/voice-connections)）。bostie 的语音引擎运行在 GIL 之外的 Rust 线程上：20 ms 时钟、解码、混音、Opus 编码、传输加密和 DAVE 都在那里完成。Python 只需保持每个 sink 的缓冲区有数据，默认是 0.2 秒的音频。bostie 可以播放文件、URL、HLS 流以及你自己的 PCM 或 Opus 数据，能混合多个音源，单独接收每位说话者的声音，还能录制通话。

### 经得起 Discord 变更的机器人 { #bots-that-survive-discords-changes }

未知的字段、枚举值、组件类型和交互类型都不会导致解码失败。每个事件，以及每个从完整载荷解码出的模型，都会保留原始 JSON；`bot.http` 和原始事件监听器可以触及 bostie 尚未建模的内容。

### 与 Discord 文档一致的类型 { #types-that-match-discords-documentation }

Discord 总会发送的字段绝不会是 `None`，部分对象也有自己的类：从命令选项解析出的成员是 `ResolvedMember`，它没有 `deaf` 和 `mute`。

### 由函数签名生成命令 { #commands-from-signatures }

选项、候选项、取值范围和自动补全都来自类型提示。名称、描述、选项数量和候选项会在装饰器运行时就按 Discord 的限制进行检查，而不是等到 Discord 拒绝同步时才发现。

### 无需 Discord 的测试 { #tests-without-discord }

`bostie.testing.FakeDiscord` 是一个本地的网关和 REST API，可以让你真实的机器人对着它运行。

## 接下来 { #where-to-go-next }

- [安装](install.md) bostie 及其构建依赖。
- 在[快速入门](quickstart.md)中编写第一个机器人。
- 指南每页讲解一个功能，从[斜杠命令](guide/commands.md)到[测试](guide/testing.md)。
- [语音](voice/index.md)部分涵盖连接、播放、原始音频和接收。
- 参考部分列出了每个 [`Bot` 选项](reference/bot-options.md)、每个[事件](reference/event-list.md)以及 [API](reference/api/bot.md)。
