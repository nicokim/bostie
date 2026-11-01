# Contributing

Thanks for helping with bostie. Questions, bug reports and feature ideas go in the GitHub issue tracker. Report a security problem privately instead, as [SECURITY.md](SECURITY.md) describes. If you want to change the public API, open an issue first so we can agree on the design before you build it. A new event, a clearer name or a missing test can go straight to a pull request.

## Setup

You need Python 3.12 or newer, [Rust](https://rustup.rs) 1.95 or newer, [uv](https://docs.astral.sh/uv/) and [CMake](https://cmake.org/download/), which builds the bundled libopus.

```sh
uv sync                    # builds the Rust extension and installs the dev tools
uv run pytest              # rebuilds the extension first if a Rust file changed
cargo test -p bostie-core
```

The first `uv sync` compiles the whole workspace and prints only `Building bostie` for a few minutes. It isn't stuck.

The zstd gateway tests need `compression.zstd` from Python 3.14, so they skip on 3.12. Run them with `uv run --python 3.14 pytest`. That rebuilds `.venv` for 3.14, and the next plain `uv run` switches it back.

## Before you open a pull request

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

`cargo fmt --all` and `uv run ruff format` fix the formatting. ruff leaves long strings and docstrings alone, though, so `ruff check` can still report E501. mypy runs in strict mode on the tests as well as the library. If you change `crates/bostie-native`, update `python/bostie/_native.pyi` to match or stubtest fails. If you change `.github/workflows`, also run `uvx --from actionlint-py actionlint`. When you add or bump a Rust dependency, regenerate the notices with `cargo about generate --locked -m crates/bostie-native/Cargo.toml -o THIRD-PARTY-NOTICES.md about.hbs`, using the cargo-about version CI pins (`cargo install cargo-about --locked --features cli --version 0.9.3`).

CI runs the tests on Linux, macOS and Windows, with Python 3.12 to 3.15 and the free-threaded 3.14t and 3.15t.

Keep a pull request to one change. Its title goes into the release notes as it is, so say what changes for someone using bostie. A pull request that changes nothing for them gets the `skip-changelog` label. A commit message is one [Conventional Commits](https://www.conventionalcommits.org) line of 100 characters at most, like `feat(events): add the GUILD_EMOJIS_UPDATE event`; the why goes in the pull request. A bug fix comes with the test that failed before it.

## Adding a gateway event

Rust needs no change: the core forwards any dispatch name Python asks for.

1. Add a class to `python/bostie/events.py` that subclasses `Event` with `kw_only=True`, and set `NAME` to the dispatch name. `MessageDelete` and `TypingStart` are short ones to copy. msgspec doesn't inherit `kw_only`, so every subclass repeats it.
2. Set `INTENTS` to the intents Discord needs to send the event. The bot uses it to warn at startup when a listener's [privileged intent](https://docs.discord.com/developers/events/gateway#privileged-intents) is missing.
3. Add the class to the `EVENTS` registry in the same file.
4. Save the `d` payload as `tests/fixtures/models/events/NAME.json`. Another shape of the same event gets a suffix, as in `GUILD_CREATE.unavailable.json`. `test_every_dispatch_decodes_to_its_event` picks fixtures up on its own, and `test_every_event_fixture_has_an_entry_in_the_registry` fails until step 3 is done.
5. Add a test to `tests/test_events.py` that decodes the fixture and checks its fields.
6. If the event changes cached state, add it to `Cache.NAMES` and `Cache.apply` in `python/bostie/cache.py`.

Discord adds fields and enum values without notice, and a bot must not break when it does. So models never reject what they don't know:

- Enums subclass `DiscordIntEnum` or `DiscordIntFlag` from `python/bostie/enums.py`, which keep unknown values.
- Every field except an id or `type` has a default, usually `None`. `test_fields_have_defaults_so_a_missing_field_never_drops_an_event` checks every model and event.
- `decoder()` in `python/bostie/models/base.py` runs msgspec in lax mode. Snowflake strings become `int` and unix timestamps become `datetime` without a converter.

If a payload still doesn't fit, `decode_event` logs a warning once per event name and returns `None`. Typed listeners skip that event; `RawEvent` listeners still get it.

## Tests

`tests/fakediscord` is a local fake of the Discord gateway and REST API. It serves the payloads in `tests/fixtures/models` and records every REST request in `fake.requests`. pytest puts `tests/` on the import path, so you import it as `fakediscord`:

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

`running(bot, fake)` starts the bot, waits for READY and closes it at the end. `fake.dispatch(name, d)` sends a dispatch, and `fake.interaction(type, data)` sends an interaction and returns the bot's answer. `fake.fail(method, route_prefix, status, json)` answers the next matching request with that status, which also covers routes the fake doesn't model. `FakeDiscord(voice=True)` adds a voice gateway and a UDP server that decrypts every RTP packet into `fake.packets`, and `move_bot` and `kick_bot` move the bot around. It doesn't speak DAVE; the Rust tests cover that. Use these fakes rather than `unittest.mock`.

Test what a user could notice: a return value, an event, a packet sent, an error raised. Log text and call order count only when they are the feature. Most counters in `crates/bostie-core/src/voice/media.rs` are there for the tests: they show why a packet was dropped, which nothing a user sees tells apart. Anything mypy, stubtest or clippy already catches needs no test.

Each behaviour gets one test, at the lowest layer that shows it. Codecs, crypto and state machines have a `mod tests` at the bottom of their Rust file, or in `<module>/tests.rs` when the file is long. Python tests cover what the Python layer adds. The end-to-end tests in `tests/test_bot_voice.py` and `crates/bostie-core/src/voice/tests.rs` are there to prove the parts are connected, not to repeat the unit tests. When two tests differ only in their inputs, make them one `pytest.mark.parametrize` table or one loop over cases.

Parsers and crypto get a round trip, a known-answer vector when one exists, and a malformed input that comes back as an error instead of a panic. Wire formats get one fixture per payload under `tests/fixtures`, taken from Discord's documentation. In `crates/bostie-core/src/voice/testdata`, `media/generate.sh` rebuilds the audio fixtures, byte for byte with ffmpeg 7.1.1, and `uv run vectors.py` prints the transport encryption vectors.

Don't wait on the clock. Pass short timeouts and intervals through configuration, not a `#[cfg(test)]` constant. The pacer tests are the exception, because real time is what they measure. Their default bound holds on a busy CI runner. To check the clock's precision, run `BOSTIE_STRICT_TIMING=1 cargo test -p bostie-core pacer` on an idle machine, which holds it to 5 ms at p99.

Name a test after what it proves, like `a_dropped_socket_resumes_with_the_last_seq_and_keeps_sending`. Python tests go in `tests/test_<module>.py`. Nothing in the suite touches the network or real Discord.

## Style

We don't write comments in code. If a block seems to need one, rename it or split it until it doesn't. Public Python API may have a docstring when the name and signature aren't enough, and one line is usually right.

`bostie-core` never depends on PyO3 and never calls into Python. Everything that faces Python lives in `bostie-native` or `python/bostie`.

No `unsafe`, and no `unwrap`, `expect`, `panic!` or slice indexing outside tests. clippy denies all of them. A panic on one of the core's threads would never reach the Python user, so return an error instead.

When you add or bump a dependency, use its latest release. A change made for speed says in the pull request how you measured the gain.
