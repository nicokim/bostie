# Bot options

Every option of `Bot(...)`, all keyword-only. The guides explain each in context.

## Gateway and identity

| Option | Default | Meaning |
|---|---|---|
| `intents` | `Intents.DEFAULT` | the [intents](../guide/gateway.md#intents) to ask for; the default has every intent but the privileged three |
| `status` | `Status.ONLINE` | the [presence](../guide/gateway.md#presence) status at login |
| `activities` | `()` | activities at login, built with `bostie.activity` |
| `afk` | `False` | whether the bot reports itself as away |
| `idle_since` | `None` | the `datetime` the bot went idle |
| `large_threshold` | 50 | 50 to 250: the member count above which Discord leaves offline members out of a guild's payload |
| `shard_count` | `"auto"` | the number of [shards](../guide/sharding.md), or `"auto"` for Discord's recommendation |
| `shard_ids` | `"all"` | the shards this process runs; needs an explicit `shard_count` |
| `before_identify` | `None` | `async def f(shard_id)` awaited before each identify, to coordinate processes |
| `session_file` | `None` | a path where `close()` saves the sessions and the next `start()` [resumes](../guide/sharding.md#resuming-after-a-restart) them |
| `guild_ready_timeout` | 2.0 | seconds without a new guild after which a shard counts as [ready](../guide/cache.md#startup) |

## Commands and interactions

| Option | Default | Meaning |
|---|---|---|
| `auto_sync` | `True` | [sync the commands](../guide/registration.md) at startup when they differ from Discord's |
| `dev_guilds` | `()` | guild ids to register commands in instead of globally |
| `contexts` | `None` | where global commands can be used, unless a command or router says otherwise |
| `integration_types` | `None` | which installs carry global commands, unless a command or router says otherwise |
| `translate` | `None` | `f(text, locale)` returning a [translation](../guide/registration.md#translations) or `None` |
| `auto_defer` | 2.5 | seconds after which an unanswered interaction is [deferred](../guide/responses.md#deferring); `None` turns it off |
| `allowed_mentions` | `AllowedMentions(parse=("users", "roles"))` | which mentions can [ping](../guide/responses.md#allowed-mentions) by default |
| `owner_ids` | `None` | the users [`is_owner`](../guide/checks.md#roles-and-owners) lets through; `None` reads the app's owner or team |

## Cache

| Option | Default | Meaning |
|---|---|---|
| `cache` | `True` | keep the [cache](../guide/cache.md); `False` keeps nothing |
| `members` | `False` | keep every member; needs `Intents.GUILD_MEMBERS` |
| `chunk_members` | `True` | with `members=True`, request the members Discord leaves out at startup |
| `presences` | `False` | keep presences; needs `Intents.GUILD_PRESENCES` |
| `max_messages` | 0 | how many of the latest messages to keep |
| `max_dm_channels` | 0 | how many of the latest DM channels to keep |

## HTTP, voice and shutdown

| Option | Default | Meaning |
|---|---|---|
| `http_settings` | `HTTPSettings()` | [rate limits, retries, timeouts and proxy](../guide/rest.md#rate-limits-retries-and-timeouts) of REST requests |
| `api_url` | `None` | the REST base URL; else `BOSTIE_API_URL`, else Discord's |
| `voice` | `voice.Defaults()` | the [voice settings](../voice/index.md#settings) every connection starts from |
| `shutdown_grace` | 0.0 | seconds [`close()`](../guide/running.md#the-lifecycle) gives running handlers before cancelling them |

`run(token=None, loop_factory=None)` and `start(token=None)` take the token, `BOSTIE_TOKEN` by default.
