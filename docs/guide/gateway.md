# Intents, presence and the gateway

The gateway is the WebSocket over which Discord sends events and the bot sends its presence and requests. bostie's Rust core keeps it connected: heartbeats, resumes, reconnects, compression and the send rate limit. This page covers what the bot asks of it.

## Intents

Discord sends an event only to a bot that asked for its intent. `Bot(intents=...)` takes an `Intents` flag, `Intents.DEFAULT` when left out: every intent except the three privileged ones.

| Privileged intent | Needed for |
|---|---|
| `GUILD_MEMBERS` | member join, update and remove events, all members in the cache, requesting every member |
| `GUILD_PRESENCES` | presence updates and presences in the cache |
| `MESSAGE_CONTENT` | the content, embeds, attachments and components of messages in guilds, except messages that mention the bot; DMs need no intent for them |

A privileged intent must also be turned on, on the **Bot** page of the Developer Portal. Without that, Discord closes the connection with code 4014 and `start()` raises `PrivilegedIntentsRequired`, whose message says where to turn it on. Add intents with `|`: `Intents.DEFAULT | Intents.MESSAGE_CONTENT`. bostie warns when a listener needs an intent the bot lacks, and raises `MissingIntent` when an option or a request needs one.

Voice needs `GUILD_VOICE_STATES`, which `DEFAULT` includes.

```python
--8<-- "docs/snippets/gateway/intent_presets.py"
```

Three presets sit next to the single intents: `Intents.DEFAULT`, also named `Intents.UNPRIVILEGED`, holds every intent but the privileged ones, `Intents.PRIVILEGED` the three privileged ones, and `Intents.ALL` both. `& ~` takes intents away from a preset, as above.

## Presence

```python
--8<-- "docs/snippets/gateway/presence_and_requests.py"
```

`Bot(status=..., activities=[...], afk=False, idle_since=None)` sets what the bot shows when it connects. `await bot.update_presence(...)` changes it on every shard, and on shards that connect later; an option left out keeps its value.

`status` is a `Status`: `ONLINE`, `IDLE`, `DND` or `INVISIBLE`. `bostie.activity` builds the activities:

| Builder | Shows |
|---|---|
| `activity.playing(name)` | Playing *name* |
| `activity.listening(name)` | Listening to *name* |
| `activity.watching(name)` | Watching *name* |
| `activity.competing(name)` | Competing in *name* |
| `activity.streaming(name, url)` | Streaming *name*, for the streaming services Discord supports |
| `activity.custom(text)` | a custom status with *text* |

Each but `custom` takes `state=`, a second line of text, and returns an `activity.ActivityDict`.

## Requests

Some data comes only through the gateway, as the answer to a request:

- `await bot.request_members(guild_id, query="ab", limit=10)` returns the members whose username or nickname starts with `query`, at most `limit` (0 to 100). `user_ids=[...]` asks for up to 100 members by id instead. With neither, it asks for every member, which needs `GUILD_MEMBERS`. `presences=True` adds their presences and needs `GUILD_PRESENCES`. The answer is a `RequestedMembers` with `members`, `presences` and `not_found`, the ids Discord did not find. Discord rate-limits this request: a refused one raises `MemberRequestRateLimited` with `retry_after`.
- `await bot.request_soundboard_sounds(guild_id)` returns the guild's soundboard sounds.
- `await bot.request_channel_info(guild_id)` returns the voice status and voice start time of the guild's channels; `fields=["status"]` asks for less.

Each waits up to 30 seconds for each part of the answer, `timeout=` for members, and raises `TimeoutError` after that. A request to a shard that is starting a new session raises `StateError`; retry once it is ready.

## Raw gateway commands

`bot.gateway.send(op, d)` sends any gateway command. Opcode 3 (presence) goes to every shard; a payload with `guild_id` goes to that guild's shard, one with `guild_ids` to each shard with its share of the ids, and anything else to shard 0. Discord's answers arrive as events, and only for event names something listens to, so pair a raw command with a `listen_raw` listener or a `wait_for`.

`bot.gateway` also gives `shard_ids`, the shards this process runs; `shard_for(guild_id)`, the shard Discord sends a guild's events to; `shard(shard_id)`, with its own `send`, `reconnect` and `latency`; `reconnect()`, which drops every connection and resumes the sessions on new ones; and `latency`, the mean heartbeat round trip. A command for a shard this process does not run raises `ShardNotRunning`, and one queued behind too many others raises `GatewayQueueFull`.

## Backpressure

The Rust core reads events as fast as Discord sends them and hands them to Python. When the event loop falls behind, a shard stops reading until Python catches up, logs a warning and sends `ShardBackpressure` with the bytes waiting. Long blocking work in a handler or listener causes it; move that work to a thread with `asyncio.to_thread`.
