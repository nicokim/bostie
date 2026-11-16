---
source: 2ee95f317499fe403f0b728d5caa99b8854b93d5
description: "用 pip 安装 bostie：支持 Python 3.12+ 的 Linux、macOS、Windows 预编译 wheel、自由线程构建、testing 附加依赖与源码构建。"
---

# 安装

![Bostie 打开一个装着星星的盒子](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

把 bostie 安装到你机器人的环境中：

```sh
pip install bostie
```

wheel 中已经包含编译好的 Rust 核心和 libopus，因此 pip 不需要编译任何东西，也不需要其他工具。

## 环境要求 { #requirements }

bostie 需要 Python 3.12 或更高版本。wheel 覆盖 3.12 及以后的所有 CPython 版本，适用于：

- x86_64 和 aarch64 上的 Linux，需要 glibc 2.28 或更高版本，或者 musl。
- Apple Silicon 和 Intel 上的 macOS。
- x86_64 上的 Windows。

Python 3.14 和 3.15 的自由线程（free-threaded）构建在上述每个平台上都有专门的 wheel。

CI 会在 Linux、macOS 和 Windows 上，用 Python 3.12、3.13、3.14 和 3.15 测试每一次提交。

## testing 附加依赖 { #the-testing-extra }

`bostie.testing` 是供测试使用的模拟 Discord，它还需要另外两个包。通过 `testing` 附加依赖安装它们：

```sh
pip install "bostie[testing]"
```

语音功能不需要额外安装任何东西：解码、Opus 和加密都已编译进 bostie。只有 [`voice.FFmpeg`](voice/playback.md#sources) 音源会运行外部程序 `ffmpeg`，它必须在 `PATH` 中。

## 从源码构建 { #build-from-source }

如果没有合适的 wheel，pip 会从 bostie 的源码分发包构建它。用 `pip install path/to/bostie` 安装仓库的克隆时也是如此。构建需要：

- Rust 1.95 或更高版本，通过 [rustup](https://rustup.rs) 安装。
- [CMake](https://cmake.org/download/) 和一个 C 编译器，用于构建 libopus。

构建过程会编译 Rust 核心和 libopus，需要几分钟。

## 检查安装 { #check-the-install }

```sh
python -m bostie
```

会打印 bostie 的版本、Python 实现及其版本，以及所在平台。提交 bug 报告时请附上这三行。在代码中，`bostie.__version__` 保存了版本号，`bostie.voice.versions()` 返回编译进当前构建的 bostie、libopus、DAVE 库和 DAVE 协议的版本。

## 下一步 { #next }

[快速入门](quickstart.md)会在 Discord 中创建应用，并运行第一个机器人。
