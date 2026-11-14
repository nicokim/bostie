# Events

`@bot.listen` takes the typed events of `bostie.events`. `await bot.wait_for(Event, check=...)` waits for the next one, and `async with bot.stream(Event, check=...) as events:` queues every match for `async for` until the block ends; bound either with `asyncio.timeout()`.

- `Starting` fires once per `start()`, when REST works and before commands sync and the gateway connects: open a database pool or include more routers there. `start()` awaits its listeners one at a time and raises what one raises. `Stopped` fires at the end of `close()` for every `start()` that sent `Starting`, once the gateway and REST are gone: close the pool there.
- `Ready` is Discord's READY: one shard started a session, and the guilds it lists arrive afterwards as `GuildCreate` events. It fires again for every new session.
- `ShardReady` follows once those guilds have arrived, with their members when the cache keeps members, or after `guild_ready_timeout` seconds without one, so the shard's guilds are in `bot.cache`. It too fires for every new session.
- `Started` fires once per `start()`, when every shard is ready. `await bot.wait_until_ready()` returns at that moment and `bot.is_ready` turns true. `Stopping` fires when `close()` begins after `Started`, and `close()` gives its listeners up to 5 s while the gateway and REST still work.
- `GuildCreate.joined` is true for a guild the bot was just added to, false for one that became available.
- Update events carry the object as the cache held it in `old`, and delete events the removed one: `GuildDelete.guild`, `RoleDelete.role`, `ThreadDelete.thread`, `MemberRemove.member`, `MessageDelete.message`.
- `ShardDisconnect` says a shard lost its connection, with the close code and whether it resumes the session.
- `ShardStop` says a shard stopped for good, because `close()` ran or Discord ended it with a close code, and `ShardBackpressure` that a shard paused reading because listeners fall behind.
- An exception in a listener goes to the `ListenerError` listeners, or to the log when there are none. `router.remove_listener(callback)` stops a listener.
- Events of your own subclass `bostie.events.BotEvent`, as in `class LevelUp(BotEvent): user_id: int`, and `bot.dispatch(LevelUp(user_id=...))` hands one to its listeners, `wait_for` and `stream`.
- Registering a listener for an event that none of the bot's intents delivers logs a warning.
- Some events only reach a Webhook Events URL, such as `ApplicationAuthorized` when a user installs the app. `bostie.verify_signature(public_key, signature, timestamp, body)` checks that Discord sent a request to such a URL or to an Interactions Endpoint URL, and `bostie.decode_webhook_event(body)` decodes its event, or returns None for Discord's PING.

`await bot.request_members(guild_id, query=..., limit=...)` or `user_ids=...` asks the gateway for members and gathers every chunk of the answer, with `presences=True` their presences too. `limit` goes up to 100 and `user_ids` takes at most 100. Without `query` or `user_ids` it asks for every member, which needs `Intents.GUILD_MEMBERS`; presences need `Intents.GUILD_PRESENCES`. It raises `bostie.MemberRequestRateLimited` with `retry_after` when Discord refuses, and `TimeoutError` when a chunk takes over `timeout` seconds, 30 by default. `await bot.request_soundboard_sounds(guild_id)` and `await bot.request_channel_info(guild_id)` ask the gateway for a guild's soundboard sounds and its channels' voice status. A request raises `StateError` when its shard starts a new session before the answer.
