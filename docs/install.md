# Install

![Bostie opening a box with a star inside](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

Install bostie into your bot's environment:

```sh
pip install bostie
```

The wheels come with the Rust core and libopus compiled, so pip compiles nothing and needs no other tool.

## Requirements

bostie needs Python 3.12 or newer. Wheels cover every CPython from 3.12 on:

- Linux on x86_64 and aarch64, with glibc 2.28 or newer, or musl.
- macOS on Apple Silicon and Intel.
- Windows on x86_64.

The free-threaded builds of Python 3.14 and 3.15 have their own wheels on each of these platforms.

CI tests every commit on Linux, macOS and Windows with Python 3.12, 3.13, 3.14 and 3.15.

## The testing extra

`bostie.testing`, the fake Discord your tests run against, needs two more packages. Install them with the `testing` extra:

```sh
pip install "bostie[testing]"
```

Voice needs nothing else: decoding, Opus and encryption are compiled into bostie. Only the [`voice.FFmpeg`](voice/playback.md#sources) source runs an external program, `ffmpeg`, and it has to be on `PATH`.

## Build from source

Where no wheel fits, pip builds bostie from its source distribution. So does installing a clone of the repository with `pip install path/to/bostie`. The build needs:

- Rust 1.95 or newer, installed with [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) and a C compiler, which build libopus.

It compiles the Rust core and libopus, which takes a few minutes.

## Check the install

```sh
python -m bostie
```

prints bostie's version, the Python implementation and version, and the platform. Paste those three lines into a bug report. `bostie.__version__` holds the version in code, and `bostie.voice.versions()` returns the versions of bostie, libopus, the DAVE library and the DAVE protocol compiled into the build.

## Next

The [quickstart](quickstart.md) creates the application in Discord and runs a first bot.
