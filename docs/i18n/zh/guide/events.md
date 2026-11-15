---
source: a5b6afe7ce2baa740ef1fd06599d2895465c3d1c
---

# 事件

![Bostie 朝着一只响铃打招呼](../assets/mascot/bostie-events.webp){ align=right width="180" loading=lazy }

Discord 通过网关事件告诉机器人发生了什么：有人发送了一条消息、有成员加入、某个身份组发生了变化。监听器是一个接收单个事件的异步函数。

```python
--8<-- "docs/snippets/events/listeners.py"
```

`@bot.listen` 读取第一个参数的类型提示，并把函数注册到该事件类上，如果是联合类型，则注册到其中每个类上。`@bot.listen(MessageUpdate)` 则直接指定类，适用于没有类型提示或类型提示更宽泛的函数。这些类位于 `bostie.events` 中，[事件列表](../reference/event-list.md)列出了每个事件类及其 Intent 和字段。[路由器](routers.md)也有同样的装饰器。

每个监听器都作为独立的任务运行，因此一个慢的监听器不会拖住其他监听器或下一个事件。任务会立即开始：监听器中第一个 `await` 之前的代码会在下一个事件被处理之前运行。`Starting` 和 `Stopped` 的监听器是例外：`start()` 和 `close()` 会逐个 await 它们。`bot.remove_listener(function)` 会把一个函数从它监听的所有事件上注销。

## 只接收你监听的事件 { #only-what-you-listen-to }

bostie 会让 Rust 核心只转发机器人中有地方用到的分发名称：你的监听器、缓存、命令和组件路由，以及正在进行的等待。其他所有事件在到达 Python 之前就被丢弃，所以什么都不监听就什么开销都没有。带类型的事件每次分发只解码一次，并且只在有人需要时才解码。

## Intent { #intents }

Discord 只把大多数事件发送给申请了相应 Intent 的机器人。`Bot(intents=...)` 默认为 `Intents.DEFAULT`，即除三个特权 Intent 之外的所有 Intent。当某个监听器注册的事件不会由机器人的任何 Intent 送达时，bostie 会记录一条警告，指出需要添加哪个 Intent，并提醒你特权 Intent 还必须在 Developer Portal 中开启。[Intent](gateway.md#intents) 一节有详细介绍。

## 旧值与缓存 { #old-values-and-the-cache }

更新事件带有 `old`，即变更之前[缓存](cache.md)中的对象，删除事件则带有被移除的对象：`RoleUpdate.old`、`ScheduledEventUpdate.old`、`MessageDelete.message`、`MemberRemove.member`。缓存中没有该对象时它们为 `None`。消息只有在设置了 `Bot(max_messages=...)` 时才会被缓存，成员只有在 `members=True` 时、在线状态只有在 `presences=True` 时才会被缓存。

## 等待事件 { #waiting-for-an-event }

```python
--8<-- "docs/snippets/events/waiting.py"
```

`await bot.wait_for(Event, check=None)` 返回该类的下一个使 `check` 返回真值的事件。`async with bot.stream(Event, check=None) as events:` 会在代码块运行期间把每个匹配的事件排入队列，供 `async for` 使用；在循环体运行期间到达的事件不会丢失。二者都没有自己的超时，所以请用 `asyncio.timeout()` 限定等待时间。`check` 抛出的异常会从等待处抛出。`bot.wait_for_component` 和 `bot.wait_for_modal` 对[组件使用](components.md#waiting-for-a-click-inside-a-handler)和[模态框提交](modals.md#waiting-for-the-submit-inside-a-handler)做同样的事。

## 原始事件与 bostie 尚未建模的事件 { #raw-events-and-events-bostie-does-not-model }

```python
--8<-- "docs/snippets/events/custom_events.py"
```

`@bot.listen_raw("NAME", ...)` 会为你给出的每个分发名称传递一个 `RawEvent`，无论该事件是否已建模；类型提示为 `RawEvent` 的监听器会收到所有分发。`RawEvent` 带有分发名称 `name`、序列号 `seq`，以及载荷 `raw`（每次都是一个新的 `dict`）或 `raw_json`（字节）。每个带类型的事件也都有 `raw` 和 `raw_json`。

当 Discord 发送的载荷与其类不匹配时，例如缺少文档中标为必需的字段，bostie 会针对每个事件名记录一次警告，带类型的监听器会跳过该事件，而 `RawEvent` 监听器仍会收到它。

## 自定义事件 { #events-of-your-own }

继承 `BotEvent` 并设置 `kw_only=True`，然后用 `bot.dispatch(event)` 发出一个实例：它的监听器、等待和流都会像对待其他事件一样收到它。没有 `NAME` 的子类以其模块和类名命名。事件类是不可变的 `msgspec` 结构体，所以字段以类型注解的形式声明。

## 生命周期事件与分片事件 { #lifecycle-and-shard-events }

bostie 会发出一些关于机器人自身的事件：围绕[生命周期](running.md#the-lifecycle)的 `Starting`、`Started`、`Stopping` 和 `Stopped`；关于每个[分片](sharding.md)的 `ShardReady`、`ShardDisconnect`、`ShardStop` 和 `ShardBackpressure`；关于故障的 `ListenerError` 和 `TaskError`。语音连接通过同一个 `@bot.listen` 发送[语音事件](../voice/index.md#events)。

## 监听器抛出异常时 { #when-a-listener-raises }

监听器中的异常会作为一个 `ListenerError` 事件发出，带有 `listener`、`event` 和 `error`。如果没有 `ListenerError` 监听器，bostie 会记录该异常及其回溯信息；如果有，记录日志就由你决定。`ListenerError` 监听器中的异常只会被记录。
