---
source: 7849bf684b355c9ddd7819d46330ce19ea09b251
---

# インストール

![星の入った箱を開ける Bostie](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

bostie は、リポジトリのクローンからビルドします。ビルドでは Rust コアと同梱の libopus をコンパイルするため、初回は数分かかります。

## 必要なもの { #requirements }

- Python 3.12 以降。3.14 と 3.15 のフリースレッド版でも動作します。
- Rust 1.95 以降。[rustup](https://rustup.rs) でインストールします。
- libopus のビルドに使う [CMake](https://cmake.org/download/) と C コンパイラ。

CI は、すべてのコミットを Linux、macOS、Windows 上の Python 3.12、3.13、3.14、3.15 でテストしています。

## クローンからインストールする { #install-from-a-clone }

リポジトリをクローンし、そのクローンを Bot の環境にインストールします。

```sh
pip install path/to/bostie
```

テストの接続先となるフェイクの Discord である `bostie.testing` には、さらに 2 つのパッケージが必要です。`testing` extra を付けてインストールしてください。

```sh
pip install "path/to/bostie[testing]"
```

ボイスにはほかに何も必要ありません。デコード、Opus、暗号化は bostie にコンパイル済みです。外部プログラム `ffmpeg` を実行するのは [`voice.FFmpeg`](voice/playback.md#sources) ソースだけで、その場合は `ffmpeg` が `PATH` 上にある必要があります。

## インストールを確認する { #check-the-install }

```sh
python -m bostie
```

を実行すると、bostie のバージョン、Python の実装とバージョン、プラットフォームが表示されます。バグ報告にはこの 3 行を貼り付けてください。コード内では `bostie.__version__` がバージョンを保持し、`bostie.voice.versions()` はビルドに組み込まれた bostie、libopus、DAVE ライブラリ、DAVE プロトコルのバージョンを返します。

## 次のステップ { #next }

[クイックスタート](quickstart.md)では、Discord でアプリケーションを作成し、最初の Bot を動かします。
