# Testing your bot

`bostie.testing` runs a fake Discord on localhost: a gateway, a REST API with the routes a bot uses most, and optionally a voice server. Your real bot connects to it, and the test drives Discord's side: it sends events and interactions, then checks what the bot sent back. It needs the `testing` extra ([Install](../install.md)), and bostie's own test suite runs on it.

```python
--8<-- "docs/snippets/testing/test_greeter.py"
```

`run(scenario)` starts a `FakeDiscord`, runs `async def scenario(fake)` against it within 10 seconds, and shuts it down; it is an ordinary function, so pytest needs no plugin. `async with running(bot, fake):` starts the bot on the fake, waits until it is ready, and closes it at the end. Point the bot at the fake with `Bot(api_url=fake.api_url)`, or set `BOSTIE_API_URL` when the bot is built elsewhere.

## Driving Discord's side

| Call | Does |
|---|---|
| `await fake.interaction(type, data)` | sends an `INTERACTION_CREATE` and returns the bot's first answer as a `Recorded` request |
| `await fake.dispatch(name, d, shard=0)` | sends any gateway event and returns its sequence number |
| `fake.expect_callback(payload)` | for an interaction you dispatch yourself: a future for its first answer |
| `await fake.send_op(op, d)` | sends a non-dispatch opcode, such as 7 (reconnect) or 9 (invalid session) |
| `await fake.close_gateway(code)`, `fake.drop_gateway()` | closes the connection with a close code, or drops it without one |
| `fake.hold_acks()` | stops answering heartbeats, as a dead connection would |
| `await fake.next_identify()`, `await fake.next_resume()` | waits for the bot's next IDENTIFY or RESUME |

Interaction types are Discord's numbers: 2 for a command, 3 for a component, 4 for autocomplete, 5 for a modal submit.

`bostie.testing.payloads` builds Discord's payloads from the examples in its documentation, with fresh ids:

- `command(name, *options)`, `option(name, type, value, focused=False)`, `subcommand(...)`, `group(...)` and `resolved()` for commands and autocomplete;
- `context_menu(type, name, target_id, resolved)` for user and message commands;
- `button(custom_id)`, `select(custom_id, *values)` and `modal(custom_id)` for components and modals;
- `message(...)`, `guild(...)`, `member(...)`, `user(...)`, `role(...)`, `channel(...)`, `voice_channel(...)`, `stage_channel(...)` and `voice_state(...)` for events, each with keyword arguments that override fields;
- `fixture(name)` for any documented payload bostie ships, and `snowflake()` for a new id.

## Checking what the bot sent

`fake.requests` lists every REST request as a `Recorded`, with `method`, `path` (relative to the API base), `query`, `headers`, `json` and, for uploads, `parts`. A `Recorded` answer to an interaction has the callback body in `json`, as above. `fake.gateway_received` is a queue of the payloads the bot sent on the gateway, and `fake.connections` the gateway connections, `Connection` objects with their `received` payloads, `shard` and `close_code`.

`fake.cdn_file(path, data)` serves `data` at a CDN URL it returns, for an attachment or asset the bot downloads, and `fake.cdn_requests` records those downloads.

The fake keeps the state those routes need, and tests can read or seed it: `fake.guilds`, `fake.channels`, `fake.users`, `fake.members`, `fake.messages`, `fake.commands` and `fake.guild_commands`, with `fake.put_count` for command syncs. `fake.ready_guilds` sets the guild ids `READY` lists.

## Failures and timing

- `fake.fail(method, route_prefix, status, json)` answers the next matching request with an error, and stubs a route the fake does not serve.
- `fake.rate_limit(method, route_prefix, scope="user", retry_after=0.1)` answers it with a 429.
- `fake.answer(method, route_prefix, body, content_type)` answers it with a body that is not JSON.
- `fake.hold(method, route_prefix)` keeps it waiting until you set the returned `asyncio.Event`; `fake.hold_identify(shard)` and `fake.hold_hello()` do the same for the gateway handshake.
- `await settle()` yields to the event loop a few times, so that callbacks scheduled by what you just did run.

To wait for something the bot does in a listener, have it [dispatch an event of its own](events.md#events-of-your-own) and wait for that, as the second test does, rather than sleeping.

`FakeDiscord(compress=True)` compresses the gateway with zstd, which needs Python 3.14's `compression.zstd`; `heartbeat_interval` sets the interval it asks for, Discord's 41 250 ms by default.

## Voice

`FakeDiscord(voice=True)` adds a voice channel and a stage channel, answers voice state updates, and runs a voice gateway and UDP server, so `ctx.voice.connect()` and `vc.play(...)` work against it. It does not speak DAVE; bostie's Rust tests cover that. The test can:

- wait for what the bot sent with `await fake.next_voice_state_update()`, `await fake.next_voice_identify()` and `await fake.wait_for_packets(count)`, which returns the decrypted RTP packets as `Packet` objects; each voice connection is a `VoiceSession` on a `VoiceSocket`, and `fake.voice_op(op, d)` sends it any voice opcode;
- act as other users with `fake.clients_connect(user_ids)`, `fake.client_disconnect(user_id)`, `fake.speaking(user_id, ssrc, flags)`, `fake.send_audio(...)`, which sends Opus packets to the bot, and `fake.send_rtcp(packet)`, which sends it an RTCP packet;
- move or disconnect the bot as a moderator would, with `fake.move_bot(channel_id)` and `fake.kick_bot()`;
- set `fake.mute`, `fake.deaf` and `fake.suppress` for the voice states it sends about the bot, and `voice_order` for the order of the two events Discord sends on a join.
