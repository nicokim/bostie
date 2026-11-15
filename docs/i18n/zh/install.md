---
source: 7849bf684b355c9ddd7819d46330ce19ea09b251
---

# 安装

![Bostie 打开一个装着星星的盒子](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

bostie 需要从仓库的克隆进行构建。构建过程会编译 Rust 核心和内置的 libopus，第一次构建需要几分钟。

## 环境要求 { #requirements }

- Python 3.12 或更高版本。3.14 和 3.15 的自由线程（free-threaded）构建同样可用。
- Rust 1.95 或更高版本，通过 [rustup](https://rustup.rs) 安装。
- [CMake](https://cmake.org/download/) 和一个 C 编译器，用于构建 libopus。

CI 会在 Linux、macOS 和 Windows 上，用 Python 3.12、3.13、3.14 和 3.15 测试每一次提交。

## 从克隆安装 { #install-from-a-clone }

克隆仓库，然后把这个克隆安装到你机器人的环境中：

```sh
pip install path/to/bostie
```

`bostie.testing` 是供测试使用的模拟 Discord，它还需要另外两个包。通过 `testing` 附加依赖安装它们：

```sh
pip install "path/to/bostie[testing]"
```

语音功能不需要额外安装任何东西：解码、Opus 和加密都已编译进 bostie。只有 [`voice.FFmpeg`](voice/playback.md#sources) 音源会运行外部程序 `ffmpeg`，它必须在 `PATH` 中。

## 检查安装 { #check-the-install }

```sh
python -m bostie
```

会打印 bostie 的版本、Python 实现及其版本，以及所在平台。提交 bug 报告时请附上这三行。在代码中，`bostie.__version__` 保存了版本号，`bostie.voice.versions()` 返回编译进当前构建的 bostie、libopus、DAVE 库和 DAVE 协议的版本。

## 下一步 { #next }

[快速入门](quickstart.md)会在 Discord 中创建应用，并运行第一个机器人。
