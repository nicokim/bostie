---
source: 104f50c5749c73fc5952a47fa18309cb11805eb3
---

# Bot 选项

`Bot(...)` 的所有选项，全部仅限关键字参数。各篇指南会在具体场景中解释每一项。

## 网关与身份 { #gateway-and-identity }

| 选项 | 默认值 | 含义 |
|---|---|---|
| `intents` | `Intents.DEFAULT` | 要申请的 [Intent](../guide/gateway.md#intents)；默认值包含除三个特权 Intent 之外的所有 Intent |
| `status` | `Status.ONLINE` | 登录时的[在线状态](../guide/gateway.md#presence) |
| `activities` | `()` | 登录时的活动，用 `bostie.activity` 构建 |
| `afk` | `False` | 机器人是否把自己报告为离开 |
| `idle_since` | `None` | 机器人进入闲置状态的 `datetime` |
| `large_threshold` | 50 | 50 到 250：成员数超过多少时，Discord 在服务器载荷中省略离线成员 |
| `shard_count` | `"auto"` | [分片](../guide/sharding.md)数量，或 `"auto"` 表示采用 Discord 的推荐值 |
| `shard_ids` | `"all"` | 本进程运行的分片；需要显式指定 `shard_count` |
| `before_identify` | `None` | 在每次 identify 之前 await 的 `async def f(shard_id)`，用于协调多个进程 |
| `session_file` | `None` | 一个路径，`close()` 把会话保存到这里，下一次 `start()` 从这里[恢复](../guide/sharding.md#resuming-after-a-restart)会话 |
| `guild_ready_timeout` | 2.0 | 连续多少秒没有新服务器到达后，分片即视为[就绪](../guide/cache.md#startup) |

## 命令与交互 { #commands-and-interactions }

| 选项 | 默认值 | 含义 |
|---|---|---|
| `auto_sync` | `True` | 启动时若命令与 Discord 上的不同，则[同步命令](../guide/registration.md) |
| `dev_guilds` | `()` | 要注册命令的服务器 ID，代替全局注册 |
| `contexts` | `None` | 全局命令可以在哪里使用，除非命令或路由器另有设置 |
| `integration_types` | `None` | 哪些安装方式带有全局命令，除非命令或路由器另有设置 |
| `translate` | `None` | 返回[翻译](../guide/registration.md#translations)或 `None` 的 `f(text, locale)` |
| `auto_defer` | 2.5 | 多少秒后对尚未回应的交互[延迟回应](../guide/responses.md#deferring)；`None` 表示关闭 |
| `allowed_mentions` | `AllowedMentions(parse=("users", "roles"))` | 默认情况下哪些提及可以[发出通知](../guide/responses.md#allowed-mentions) |
| `owner_ids` | `None` | [`is_owner`](../guide/checks.md#roles-and-owners) 放行的用户；`None` 表示读取应用的所有者或团队 |

## 缓存 { #cache }

| 选项 | 默认值 | 含义 |
|---|---|---|
| `cache` | `True` | 保留[缓存](../guide/cache.md)；`False` 表示什么都不缓存 |
| `members` | `False` | 缓存所有成员；需要 `Intents.GUILD_MEMBERS` |
| `chunk_members` | `True` | 在 `members=True` 时，于启动时请求 Discord 省略的成员 |
| `presences` | `False` | 缓存在线状态；需要 `Intents.GUILD_PRESENCES` |
| `max_messages` | 0 | 缓存最新消息的条数 |
| `max_dm_channels` | 0 | 缓存最新私信频道的个数 |

## HTTP、语音与关闭 { #http-voice-and-shutdown }

| 选项 | 默认值 | 含义 |
|---|---|---|
| `http_settings` | `HTTPSettings()` | REST 请求的[速率限制、重试、超时和代理](../guide/rest.md#rate-limits-retries-and-timeouts) |
| `api_url` | `None` | REST 基础 URL；否则使用 `BOSTIE_API_URL`，再否则使用 Discord 的地址 |
| `voice` | `voice.Defaults()` | 每个连接的初始[语音设置](../voice/index.md#settings) |
| `shutdown_grace` | 0.0 | [`close()`](../guide/running.md#the-lifecycle) 在取消正在运行的处理函数之前给它们的秒数 |

`run(token=None, loop_factory=None)` 和 `start(token=None)` 接受令牌，默认取自 `BOSTIE_TOKEN`。
