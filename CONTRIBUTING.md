# Contributing

## Setup

You need [Rust](https://rustup.rs) and [uv](https://docs.astral.sh/uv/).

```sh
uv sync          # builds the Rust extension and installs dev tools
uv run pytest    # Python tests (rebuilds the extension when Rust files change)
cargo test -p bostie-core
```

## Before opening a pull request

```sh
cargo fmt --all
cargo clippy --workspace --all-targets -- -D warnings
uv run ruff format
uv run ruff check
uv run mypy
uv run python -m mypy.stubtest bostie._native --allowlist stubtest-allowlist.txt
```

CI runs the same commands, plus the test suite on Linux, macOS and Windows.

## Live check against real Discord

`tests/live/live_check.py` runs the checks that need real Discord. pytest never collects it and CI never runs it. You need a bot in a test server:

1. In the Discord Developer Portal, create an application with a bot. Invite it to a test server with the `bot` and `applications.commands` scopes, and let it send messages and attach files in one channel.
2. Turn on Developer Mode in Discord to copy the server and channel ids, then run:

   ```sh
   BOSTIE_TOKEN=... BOSTIE_LIVE_GUILD=<server id> BOSTIE_LIVE_CHANNEL=<channel id> uv run python tests/live/live_check.py
   ```

3. When the script asks, within 120 s: type `/bostie-check`, pick a suggested flavor, send the command and click its button.

Each step prints PASS or FAIL, and the script exits with a non-zero status if any step fails. It registers `/bostie-check` as a guild command in the test server and leaves it there.

The sync is a bulk overwrite, so it replaces every guild command the application already has in that server. Use a bot or server without guild commands you care about. If you can't, save them first (`GET /applications/{application_id}/guilds/{guild_id}/commands`) and `PUT` them back afterwards.

## Style

- Write code that explains itself through names and structure. Don't add comments.
- Public Python API may have a short docstring when the name and signature are not enough. One line is usually right.
- `bostie-core` never depends on PyO3 and never calls into Python. Python-facing glue lives in `bostie-native` and `python/bostie`.
- No `unsafe`. No `unwrap`, `expect`, `panic!` or slice indexing outside tests; clippy enforces this because a panic on a background thread is invisible to Python users.
- Discord payloads are decoded leniently: never reject unknown fields or enum values.
- Use the latest release of every dependency. Performance claims need a benchmark.
- Rust tests go in a `#[cfg(test)]` module next to the code. Python tests go in `tests/`.

## Design

`docs/research/00-RESUMEN.md` describes the architecture and the reasons behind it (in Spanish for now). The numbered reports next to it hold the details.
