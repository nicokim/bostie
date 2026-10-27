# lyrecord

A Discord library for Python with a Rust core, built around voice that keeps working.

> **Status:** pre-alpha. Nothing here is usable for bots yet.

## Goals

- **Voice first.** Discord requires end-to-end encrypted voice (DAVE) since March 2026, and most Python libraries still struggle with it, especially when receiving audio. lyrecord will run the whole voice path in Rust, outside the GIL, so audio timing never depends on what your Python code is doing.
- **Survives Discord changes.** Models will be tolerant: unknown fields, enum values and components never break decoding, every object keeps its raw payload, and raw escape hatches reach new events and endpoints before lyrecord models them.
- **Fast.** The gateway, compression, HTTP and rate limiting run in Rust; Python decodes raw JSON into compact `msgspec` models. Every performance claim comes with a benchmark against other libraries.

## Layout

| Path | What lives there |
|---|---|
| `crates/lyrecord-core` | Rust core: async runtime, networking, protocol. No Python dependency. |
| `crates/lyrecord-native` | Thin PyO3 bindings exposing the core as `lyrecord._native`. |
| `python/lyrecord` | The public Python API. |
| `docs/research` | The research behind the design, including benchmarks against other libraries. |

## License

Licensed under either of [Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT), at your option.
