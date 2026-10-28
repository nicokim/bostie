# Contributing

## Setup

You need Python 3.12 or newer, [Rust](https://rustup.rs) 1.95 or newer, and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                    # builds the Rust extension and installs the dev tools
uv run pytest              # Python tests; rebuilds the extension when Rust files change
cargo test -p bostie-core  # Rust tests
```

The first `uv sync` compiles the whole Rust workspace and prints only `Building bostie`, so it can look stuck for a few minutes.

The project uses Python 3.12 by default. The tests of zstd-compressed gateway traffic need `compression.zstd`, new in Python 3.14, so they skip on older versions. `uv run --python 3.14 pytest` runs them. It rebuilds `.venv` for 3.14, and the next plain `uv run` switches it back.

## Before opening a pull request

Run what CI runs:

```sh
uv sync --locked
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test -p bostie-core --locked
uv run ruff format --check
uv run ruff check
uv run mypy
uv run python -m mypy.stubtest bostie._native --allowlist stubtest-allowlist.txt
uv run pytest
```

- `cargo fmt --all` and `uv run ruff format` apply the formatting. `ruff format` does not wrap long strings or docstrings, so `ruff check` can still report E501.
- `uv run mypy` checks `python/bostie`, `tests` and `benches` in strict mode.
- stubtest checks that `python/bostie/_native.pyi` matches the compiled `bostie._native` module. If you change `crates/bostie-native`, update the `.pyi`.
- If you change `.github/workflows`, also run `uvx --from actionlint-py actionlint`.

CI runs the tests on Linux, macOS and Windows, with Python 3.12, 3.13, 3.14, 3.15, 3.14t and 3.15t. A `t` build is free-threaded CPython, which runs without the GIL.

## How an event reaches a listener

Terms the code uses:

- **delivery**: one item the Rust core hands to Python: a dispatch, a REST response or failure, or a connection status. It is `Delivery` in `crates/bostie-core/src/inbox.rs`, and Python receives it as a tuple typed in `python/bostie/_native.pyi`.
- **inbox**: the queue of deliveries waiting for Python (`Inbox` in `inbox.rs`).
- **bell**: a socket pair (`crates/bostie-core/src/bell.rs`). The core writes one byte to it when the inbox gets its first delivery since the last drain. Python's event loop watches the other end.
- **drain**: Python taking every queued delivery at once (`Core.drain()`).
- **ticket**: the number that identifies a REST request. Its response or failure delivery carries the same ticket.
- **filter**: the dispatch names Python wants. The shard skips the others without copying their payload.

The path of one dispatch:

1. `Bot.start` in `python/bostie/bot.py` creates a `Bridge` (`python/bostie/_bridge.py`). The bridge calls `Core.create` in `crates/bostie-native/src/lib.rs`, which calls `Core::new` in `crates/bostie-core/src/lib.rs`. That takes the shared tokio runtime and builds the bell and the inbox. The bridge then watches the bell with `loop.add_reader`. The core never calls into Python.
2. `Bot._dispatch_names` sends the filter through `GatewayClient.filter` (`python/bostie/gateway.py`) to `Gateway::set_filter` (`crates/bostie-core/src/gateway/mod.rs`). It is sent again whenever the listeners or the `wait_for` waiters change.
3. `GatewayClient.start` calls `Gateway::start`, which spawns `Shard::run` (`gateway/shard.rs`). It connects, identifies or resumes, and reconnects with backoff.
4. `Shard::serve` waits on the socket, the heartbeat and outgoing commands. For each message, `Shard::on_message` inflates compressed frames (`gateway/inflate.rs`), and `Frame::parse` (`gateway/envelope.rs`) reads `op`, `s` and `t`. It copies `d` only when the filter wants the event.
5. `Shard::forward` pushes a `Delivery::Dispatch` with `Inbox::push`. When the inbox already holds 64 MiB of dispatches, the shard holds the frame and stops reading the socket until Python drains, so TCP pushes back on Discord.
6. The bell wakes `Bridge._on_bell`, which calls `core.drain()`. A response or failure settles the future waiting on its ticket. Everything else goes to `Bot._on_gateway`.
7. `Bot._dispatch` decodes the payload at most once per event class (`decode_event` in `python/bostie/events.py`). It then updates the READY state, the cache, the `wait_for` waiters and the interaction routes, in that order, and last starts one task per listener.

## Adding a gateway event

1. Add a class to `python/bostie/events.py` that subclasses `Event` with `kw_only=True`, and set `NAME` to the dispatch name. `MessageDelete` and `TypingStart` are short ones to copy. Every subclass repeats `kw_only=True` because msgspec does not inherit it.
2. Set `INTENTS` to the intents Discord needs to send the event. At startup, the bot warns about a listener whose [privileged intent](https://docs.discord.com/developers/events/gateway#privileged-intents) is missing.
3. Add the class to the `EVENTS` registry in `events.py`.
4. Add a fixture, `tests/fixtures/models/events/NAME.json`, that holds only the `d` payload. A second shape goes in `NAME.variant.json`. `test_every_dispatch_decodes_to_its_event` picks it up by itself, and `test_every_event_fixture_has_an_entry_in_the_registry` fails until step 3 is done.
5. Add a test to `tests/test_events.py` that decodes the fixture and checks its fields.
6. If the event changes cached state, add it to `Cache.NAMES` and `Cache.apply` in `python/bostie/cache.py`.

Rust needs no change: the core forwards any dispatch name in the filter. Follow the decoding rules below.

## Decoding rules

- Never reject unknown fields or enum values. Enums subclass `DiscordIntEnum` or `DiscordIntFlag` from `python/bostie/enums.py`, which keep unknown values.
- Every field but an id or `type` has a default, usually `None`, so a payload without it does not drop the event. `test_fields_have_defaults_so_a_missing_field_never_drops_an_event` checks every model and event.
- `decoder()` in `python/bostie/models/base.py` uses msgspec's lax mode. It turns [snowflake](https://docs.discord.com/developers/reference#snowflakes) strings into `int` and unix timestamps into `datetime`, so fields use those types without a converter.
- When a payload does not fit its event, `decode_event` returns `None` and logs a warning, once per event name. Typed listeners skip that event, and `RawEvent` listeners still get it.

## Testing without Discord

`tests/fakediscord` is a local fake of the Discord gateway and REST API, for tests only. It serves the payloads in `tests/fixtures/models` and records every REST request in `fake.requests`.

- `run(scenario)` runs `async def scenario(fake)` against a new `FakeDiscord`.
- `async with running(bot, fake):` starts the bot on the fake, waits for READY, and closes the bot at the end.
- `await fake.dispatch(name, d)` sends a dispatch. `await fake.interaction(type, data)` sends an interaction and returns the bot's answer as a recorded request.
- `fake.fail(method, route_prefix, status, json)` answers the next matching request with `status` and `json`. It also stubs routes the fake does not model.

pytest puts `tests/` on the import path, so tests import it as `fakediscord`:

```python
from fakediscord import FakeDiscord, payloads, run, running

from bostie import Bot, Context


def test_ping_answers_pong() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.command
        async def ping(ctx: Context) -> None:
            """Answer pong."""
            await ctx.respond("pong")

        async with running(bot, fake):
            reply = await fake.interaction(2, payloads.command("ping"))
            assert reply.json["data"]["content"] == "pong"

    run(scenario)
```

## Live check against real Discord

`tests/live/live_check.py` runs the checks that need real Discord. pytest never collects it and CI never runs it. You need a bot in a test server:

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application with a bot. Invite it to a test server with the `bot` and `applications.commands` scopes, and let it send messages and attach files in one channel.
2. Turn on Developer Mode in Discord to copy the server and channel ids, then run:

   ```sh
   BOSTIE_TOKEN=... BOSTIE_LIVE_GUILD=<server id> BOSTIE_LIVE_CHANNEL=<channel id> uv run python tests/live/live_check.py
   ```

3. When the script asks, within 120 s: type `/bostie-check`, pick a suggested flavor, send the command and click its button.

Each step prints PASS or FAIL, and the script exits with a non-zero status if any step fails. It registers `/bostie-check` as a guild command in the test server and leaves it there.

The sync is a bulk overwrite, so it replaces every guild command the application already has in that server. Use a bot or server without guild commands you care about. If you can't, save them first (`GET /applications/{application_id}/guilds/{guild_id}/commands`) and `PUT` them back afterwards.

## Style

- Write code that explains itself through names and structure. Don't add comments: they go stale, and names and tests don't.
- Public Python API may have a short docstring when the name and signature are not enough. One line is usually right.
- `bostie-core` never depends on PyO3 and never calls into Python. Python-facing glue lives in `bostie-native` and `python/bostie`.
- No `unsafe`. No `unwrap`, `expect`, `panic!` or slice indexing outside tests; clippy enforces this because a panic on a background thread is invisible to Python users.
- Use the latest release of every dependency. Performance claims need a benchmark in `benches/`.
- Rust tests go in a `#[cfg(test)]` module next to the code. Python tests go in `tests/`.
- Every change in behaviour comes with a test that fails without it.

## Your first contribution

- A new event, a clearer name or a missing test is a good first change.
- Open an issue before a change to the public API, so the design can be agreed first.
- Write commit messages with a short imperative subject, then a body that explains why.
