---
render_macros: true
source: 4004163cb60675c429361e3c0739c96ffdfbc70f
---

# クイックスタート

![ターミナルにコードを打ち込む Bostie](assets/mascot/bostie-quickstart.webp){ align=right width="180" loading=lazy }

このページでは、Discord アプリケーションを作成してサーバーに追加し、スラッシュコマンドを 1 つ持つ Bot を動かします。bostie が[インストール済み](install.md)であることを前提とします。

{% include "callout.md" %}

## アプリケーションを作成する { #create-the-application }

1. [Developer Portal](https://discord.com/developers/applications) を開き、アプリケーションを作成します。
2. アプリケーションの **Bot** ページでトークンをリセットし、コピーします。トークンを持つ人は誰でも Bot を操作できるため、コードやリポジトリには含めないでください。
3. **Installation** ページで、サーバーインストールに `applications.commands` と `bot` のスコープを追加します。次にインストールリンクを開き、自分が管理するサーバーにアプリを追加します。

## Bot を書く { #write-the-bot }

次の内容を `bot.py` として保存します。

```python
--8<-- "docs/snippets/quickstart/hello_bot.py"
```

`@bot.command` は、関数をスラッシュコマンド `/hello` に変えます。コマンド名は関数名、説明は docstring の 1 行目です。`ctx` はコマンドの 1 回の使用を表す [`Context`](guide/responses.md) で、誰がどこで実行したか、そして応答するためのメソッドを持ちます。

## 実行する { #run-it }

トークンを環境変数 `BOSTIE_TOKEN` で渡し、ファイルを実行します。

```sh
export BOSTIE_TOKEN="your token"
python bot.py
```

`bot.run()` は INFO レベルでロギングを設定して接続し、定義したコマンドと Discord に登録済みのコマンドを比較します。初回の起動では `/hello` を登録して `commands synced (global): added ['hello'], ...` をログに出力します。以降の起動では、コマンドが変わらない限り何も送信しません。サーバーで `/hello` と入力すると、Bot が応答します。

Ctrl+C、またはプロセスマネージャーからの SIGTERM で、Bot はきれいに終了します。

## オプションを追加する { #add-an-option }

`ctx` の後に続くパラメーターはコマンドのオプションになり、その型は型ヒントで決まります。

```python
--8<-- "docs/snippets/quickstart/roll_bot.py"
```

`sides` は 2 から 100 までの整数オプションで、既定値は 6 です。そのため Discord はこれを省略可能なオプションとして表示し、範囲外の値は Bot に届く前に拒否します。Bot を再起動すると、変更したコマンドが送信されます。

## 次のステップ { #next }

- [スラッシュコマンド](guide/commands.md)では、すべてのオプションの型、選択肢、オートコンプリート、サブコマンドを扱います。
- [インタラクションへの応答](guide/responses.md)では、`respond`、遅延応答、フォローアップ、エフェメラルな応答を説明します。
- [コマンドの登録](guide/registration.md)では、作業中のコマンドを 1 つのテスト用サーバーに登録する方法を示します。
- [Bot の実行](guide/running.md)では、トークン、ロギング、Bot のライフサイクル、ほかのプログラムの中で Bot を動かす方法を扱います。
