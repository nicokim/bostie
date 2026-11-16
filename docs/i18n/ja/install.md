---
source: 2ee95f317499fe403f0b728d5caa99b8854b93d5
description: "pip で bostie を導入。Linux、macOS、Windows の wheel、Python 3.12 以降、フリースレッド版、testing extra、ソースビルド。"
---

# インストール

![星の入った箱を開ける Bostie](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

bostie を Bot の環境にインストールします。

```sh
pip install bostie
```

wheel には Rust コアと libopus がコンパイル済みで含まれているため、pip は何もコンパイルせず、ほかのツールも必要ありません。

## 必要なもの { #requirements }

bostie には Python 3.12 以降が必要です。wheel は 3.12 以降のすべての CPython に対応しており、次の環境向けに用意されています。

- x86_64 と aarch64 の Linux（glibc 2.28 以降、または musl）。
- Apple Silicon と Intel の macOS。
- x86_64 の Windows。

Python 3.14 と 3.15 のフリースレッド版にも、これらすべての環境向けに専用の wheel があります。

CI は、すべてのコミットを Linux、macOS、Windows 上の Python 3.12、3.13、3.14、3.15 でテストしています。

## testing extra { #the-testing-extra }

テストの接続先となるフェイクの Discord である `bostie.testing` には、さらに 2 つのパッケージが必要です。`testing` extra を付けてインストールしてください。

```sh
pip install "bostie[testing]"
```

ボイスにはほかに何も必要ありません。デコード、Opus、暗号化は bostie にコンパイル済みです。外部プログラム `ffmpeg` を実行するのは [`voice.FFmpeg`](voice/playback.md#sources) ソースだけで、その場合は `ffmpeg` が `PATH` 上にある必要があります。

## ソースからビルドする { #build-from-source }

合う wheel がない場合、pip は bostie をソース配布物からビルドします。リポジトリのクローンを `pip install path/to/bostie` でインストールする場合も同じです。ビルドには次のものが必要です。

- Rust 1.95 以降。[rustup](https://rustup.rs) でインストールします。
- libopus のビルドに使う [CMake](https://cmake.org/download/) と C コンパイラ。

ビルドでは Rust コアと libopus をコンパイルするため、数分かかります。

## インストールを確認する { #check-the-install }

```sh
python -m bostie
```

を実行すると、bostie のバージョン、Python の実装とバージョン、プラットフォームが表示されます。バグ報告にはこの 3 行を貼り付けてください。コード内では `bostie.__version__` がバージョンを保持し、`bostie.voice.versions()` はビルドに組み込まれた bostie、libopus、DAVE ライブラリ、DAVE プロトコルのバージョンを返します。

## 次のステップ { #next }

[クイックスタート](quickstart.md)では、Discord でアプリケーションを作成し、最初の Bot を動かします。
