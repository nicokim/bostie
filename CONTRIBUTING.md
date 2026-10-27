# Contributing

## Setup

You need [Rust](https://rustup.rs) and [uv](https://docs.astral.sh/uv/).

```sh
uv sync          # builds the Rust extension and installs dev tools
uv run pytest    # Python tests (rebuilds the extension when Rust files change)
cargo test -p lyrecord-core
```

## Before opening a pull request

```sh
cargo fmt --all
cargo clippy --workspace --all-targets -- -D warnings
uv run ruff format
uv run ruff check
uv run mypy
uv run python -m mypy.stubtest lyrecord._native --allowlist stubtest-allowlist.txt
```

CI runs the same commands, plus the test suite on Linux, macOS and Windows.

## Style

- Write code that explains itself through names and structure. Don't add comments.
- Public Python API may have a short docstring when the name and signature are not enough. One line is usually right.
- `lyrecord-core` never depends on PyO3 and never calls into Python. Python-facing glue lives in `lyrecord-native` and `python/lyrecord`.
- No `unsafe`. No `unwrap`, `expect`, `panic!` or slice indexing outside tests; clippy enforces this because a panic on a background thread is invisible to Python users.
- Discord payloads are decoded leniently: never reject unknown fields or enum values.
- Use the latest release of every dependency. Performance claims need a benchmark.
- Rust tests go in a `#[cfg(test)]` module next to the code. Python tests go in `tests/`.

## Design

`docs/research/00-RESUMEN.md` describes the architecture and the reasons behind it (in Spanish for now). The numbered reports next to it hold the details.
