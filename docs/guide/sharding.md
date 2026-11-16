---
description: "Shard a Discord bot in one process or across several, coordinate identifies between processes, follow shard events, and resume sessions after a restart."
---

# Sharding and resuming

![Three Bosties running in a row](../assets/mascot/bostie-running.webp){ align=right width="220" loading=lazy }

Discord splits a bot's guilds across gateway connections called shards: guild `g` belongs to shard `(g >> 22) % shard_count`. A bot in more than 2500 guilds must shard; Discord closes an unsharded connection with code 4011 and `start()` raises `ShardingRequired`.

## Shards in one process

`Bot(shard_count="auto")`, the default, asks Discord how many shards to use and runs them all. Nothing else is needed: bostie starts the shards within Discord's identify limits, and every shard sends its events to the same listeners, cache and handlers. `event.shard_id` says which shard an event came on.

## Shards across processes

```python
--8<-- "docs/snippets/sharding/cluster.py"
```

`shard_count=16` fixes the total, and `shard_ids` picks the shards this process runs; `shard_ids` needs an explicit `shard_count`, and its ids must be distinct and within `range(shard_count)`.

Discord allows a limited number of identifies at a time per bot. bostie respects that limit within one process; across processes, `before_identify` coordinates them. bostie awaits `before_identify(shard_id)` before each identify, so it can wait for a slot in a lock your processes share. In the example, `identify_lock` stands in for that shared lock, held for the 5 seconds Discord asks between identifies of one bucket. If the hook raises, bostie logs the error and identifies anyway.

## Shard events

| Event | Fields | Sent when |
|---|---|---|
| `ShardReady` | `shard_id` | a shard's new session is ready and its guilds have arrived |
| `ShardDisconnect` | `shard_id`, `code`, `reason`, `resuming` | a connection dropped; with `resuming`, the shard resumes its session and replays the events it missed, else it starts a new session |
| `ShardStop` | `shard_id`, `code`, `reason` | a shard stopped for good, because `close()` ran or Discord ended it |
| `ShardBackpressure` | `shard_id`, `queued_bytes` | a shard paused reading because the event loop falls behind |

Reconnects and resumes need nothing from you. `bot.latency` is the mean heartbeat round trip over the shards, and `bot.gateway.shard(shard_id).latency` one shard's.

## Resuming after a restart

A restart normally starts a new session on every shard, and the events of the minutes in between are lost. With `Bot(session_file="sessions.json")`, `close()` keeps the sessions open and writes them to the file. The next `start()` reads the file, deletes it, and resumes each saved session that matches the current `shard_count` and shard ids, so Discord replays the events that happened while the bot was down. A file that cannot be read is logged and ignored, and shards without a saved session identify.

`bot.run()` closes on Ctrl+C and SIGTERM, so a restart by a process manager or a deploy saves the sessions. A crash does not, and the next start identifies. An interaction replayed more than 5 seconds after it was created is skipped with a warning, because its token can no longer be answered in time.
