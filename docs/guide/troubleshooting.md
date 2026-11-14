# Troubleshooting

bostie logs a warning for each mistake it can see. This page lists them by what you observe, with the log line to look for and the fix.

## A command says "The application did not respond"

| Log line | Cause | Fix |
|---|---|---|
| `nothing handles interaction ... (command 'name')` | Discord still has a command the running bot does not define, often after a rename with `auto_sync=False`, or the command lives in another process | sync the commands ([Registering commands](registration.md)) |
| `nothing handles interaction ... (custom_id '...')` | no `on_component` or `on_modal` template matches the custom id | check the template against the ids you send; build them with `route.id(...)` |
| `... returned without answering` | the handler finished without `respond`, `defer` or `modal` | answer on every path, including early returns |
| `... returned after deferring without answering` | it deferred and never answered, so "thinking…" stays until the token expires | call `respond` after the slow work |
| `interaction ... reached its handler ... s after it was received; something is blocking the event loop` | blocking code in a handler or listener held the event loop past Discord's 3 seconds | move the work to `asyncio.to_thread`, or lower `auto_defer` |
| `interaction ... was replayed ... s after it was created; its token is dead` | the interaction came in a resumed session's replay, too late to answer | nothing; the user can run it again |

An unhandled exception in a handler is logged with its traceback, and the user sees "Something went wrong.": add an [error hook](checks.md#error-hooks) to answer something better.

## Events do not arrive

- `... listens to EVENT, which needs the INTENT intent; add it to Bot(intents=...)`: the bot did not ask for the intent. Add it, and for a privileged intent also turn it on in the Developer Portal.
- `start()` raises `PrivilegedIntentsRequired`: the bot asked for a privileged intent the portal does not allow. Turn it on under **Bot** → **Privileged Gateway Intents**, or remove it from `intents`.
- `message.content` is empty in guilds: the `MESSAGE_CONTENT` intent is missing.
- `EVENT has an unexpected shape (...); typed listeners skip it`: Discord sent a payload that does not fit the event class, logged once per event name. `RawEvent` listeners still get it; please report the line.
- `old` is `None` on update events: the cache did not hold the object; see [Cache](cache.md#what-it-keeps) for what it keeps.

## The gateway

- `shard N paused reading: ... bytes of events are waiting because the event loop is not keeping up`: something blocks the event loop; see [Backpressure](gateway.md#backpressure).
- `close code N (...) means Discord refused what bostie sent; this is likely a bostie bug, please report it`: codes 4001, 4002, 4005 and 4008 mean bostie sent something Discord refused.
- `the members of guild N stopped arriving; the cache lacks some`: a member request for the cache timed out after 30 seconds per chunk.
- `start()` raises `InvalidToken`: the token is wrong or was reset; copy it again from the **Bot** page.

## REST

- `N invalid requests (401, 403, 429) in the last 10 minutes; Discord bans the IP at 10 000`: the bot keeps making requests Discord refuses, usually missing permissions or a dead webhook. Find the failing calls in the exceptions they raise.
- `RateLimited` raised without a request reaching Discord: the route stays limited for longer than `max_rate_limit_wait`; raise it in [`HTTPSettings`](rest.md#rate-limits-retries-and-timeouts) or retry later.

## Answers

- `ephemeral=... is ignored because the message being edited is ...`: a message cannot switch between ephemeral and public after it was sent.
- `Discord allows 5 follow-ups to an interaction of an app installed only to the user`: the sixth follow-up of a user-installed app fails.
- `the error hook ... left interaction ... unanswered; bostie answered 'Something went wrong.'`: make the hook answer.

## Voice

- `nobody hears the bot in guild N: it is server muted`, or `it is suppressed on this stage; use vc.stage('request')`: a moderator muted the bot, or it is an audience member on a stage.
- `a moderator deafened the bot in guild N, so Discord sends it no audio`: receivers get nothing until it is undeafened.
- `VoiceConnectError` with reason `TIMEOUT`: Discord did not answer the join within `timeout` seconds; the channel may be full, or the bot lacks the Connect permission.
- `MissingIntent: voice needs Intents.GUILD_VOICE_STATES`: voice needs that intent, which `Intents.DEFAULT` includes.

## Reporting a bug

`python -m bostie` prints the versions to include, and `bostie.voice.versions()` adds libopus and DAVE for voice problems. Set the relevant logger to `DEBUG` before starting the bot, as [Logging](running.md#logging) shows, and include the lines around the problem with the token removed.
