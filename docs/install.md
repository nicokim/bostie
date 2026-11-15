# Install

![Bostie opening a box with a star inside](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

bostie is not on PyPI yet, so you build it from a clone of its repository. The build compiles the Rust core and a bundled libopus, which takes a few minutes the first time.

## Requirements

- Python 3.12 or newer. The free-threaded builds of 3.14 and 3.15 work too.
- Rust 1.95 or newer, installed with [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) and a C compiler, which build libopus.

CI tests every commit on Linux, macOS and Windows with Python 3.12, 3.13, 3.14 and 3.15.

## Install from a clone

Clone the repository, then install the clone into your bot's environment:

```sh
pip install path/to/bostie
```

`bostie.testing`, the fake Discord your tests run against, needs two more packages. Install them with the `testing` extra:

```sh
pip install "path/to/bostie[testing]"
```

Voice needs nothing else: decoding, Opus and encryption are compiled into bostie. Only the [`voice.FFmpeg`](voice/playback.md#sources) source runs an external program, `ffmpeg`, and it has to be on `PATH`.

## Check the install

```sh
python -m bostie
```

prints bostie's version, the Python implementation and version, and the platform. Paste those three lines into a bug report. `bostie.__version__` holds the version in code, and `bostie.voice.versions()` returns the versions of bostie, libopus, the DAVE library and the DAVE protocol compiled into the build.

## Next

The [quickstart](quickstart.md) creates the application in Discord and runs a first bot.
