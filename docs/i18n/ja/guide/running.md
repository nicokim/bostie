---
source: fecb554b875bf1b19567e85926b3784beca4dba5
---

# Bot を実行する

![紙吹雪でお祝いする Bostie](../assets/mascot/bostie-celebrating.webp){ align=right width="180" loading=lazy }

## `bot.run()` { #botrun }

ほとんどの Bot では、`bot.run()` がプログラムのすべてです。ルートロガーにまだハンドラーがなければ、INFO レベルで stderr に出力するようにロギングを設定し、イベントループを開始して、Bot が停止するまで実行します。Ctrl+C と、メインスレッドでは SIGTERM によって、Bot はクリーンに終了します。

トークンは環境変数 `BOSTIE_TOKEN` か、`bot.run(token)` から渡します。先頭の `Bot ` は取り除かれ、トークンがない場合は `ValueError` を送出します。`run` は `loop_factory` も受け取ります。これはイベントループを作成する関数で、`asyncio.run` にそのまま渡されます。

## ライフサイクル { #the-lifecycle }

`run()` の裏で実際の処理を行うのは `bot.start()` です。

1. `Starting` イベントを送り、そのリスナーを 1 つずつ順に await します。ここでは REST は使えますが、ゲートウェイは使えません。例外を送出したリスナーがあると、起動は中止されます。
2. `auto_sync=False` でない限り、[コマンドを同期](registration.md)します。
3. ゲートウェイのシャードに接続します。すべてのシャードの準備が整い、そのサーバーが届くと、`Started` を一度だけ送り、[バックグラウンドタスク](tasks.md)を開始して、`wait_until_ready()` が戻るようにします。
4. `close()` まで、または Discord がゲートウェイを完全に閉じるまでイベントを処理します。後者の場合は `InvalidToken` や `PrivilegedIntentsRequired` などの例外を送出します。

```python
--8<-- "docs/snippets/running/lifecycle_events.py"
```

`await bot.close()` は次の順序で終了処理を行います。

1. `Started` が送られていた場合は `Stopping` を送り、ゲートウェイがまだ開いている間、そのリスナーに最大 5 秒の時間を与えます。
2. すべてのボイスチャンネルから退出し、各シャードを閉じます。シャードはクローズコード 1000 でセッションを終了するか、[再開（resume）](sharding.md#resuming-after-a-restart) のためにセッションを保存します。
3. バックグラウンドタスクをキャンセルします。まだ実行中のハンドラーには、キャンセルされるまで `shutdown_grace` 秒（既定値は 0）の猶予が与えられます。
4. HTTP クライアントを閉じて `Stopped` を送り、そのリスナーを await します。この時点で `bot.http`、`bot.rest`、`bot.gateway` は使えなくなります。

`close()` はどこからでも、何度でも、`start()` の実行中にも呼び出せます。その後で Bot を再び起動することもできます。`async with bot:` は、ブロックの終わりで Bot を閉じます。

## 別のプログラムの中で動かす { #inside-another-program }

プログラムがすでにイベントループを動かしている場合は、Bot をタスクとして起動します。

```python
--8<-- "docs/snippets/running/inside_asyncio.py"
```

`await bot.wait_until_ready()` は `Started` が送られた時点で戻り、すでに送られていればすぐに戻ります。先に Bot が停止した場合は `StateError` を送出し、そのうえで `start()` のタスクを await すると停止の原因となった例外が送出されます。`bot.is_ready` は、待たずに同じことを教えてくれます。[Web アプリのサンプル](../examples.md#web-app)では、Web サーバーの lifespan の中で Bot を動かし、Web のルートからメッセージを投稿しています。

HTTP 経由でインタラクションに応答するだけの Bot には、ゲートウェイは必要ありません。[HTTP インタラクション](http-interactions.md)を参照してください。`await bot.open()` はその方式で Bot を起動してすぐに戻ります。HTTP クライアントを開き、アプリケーションと Bot のユーザーを取得し、`Starting` を送り、コマンドを同期し、バックグラウンドタスクを開始して、アプリケーションを返します。`ASGIApp` がこれを呼び出し、`close()` で終了します。REST API を呼び出すだけのスクリプトでは [`RESTClient`](rest.md#without-a-bot) を使います。

## 実行中 { #while-it-runs }

| 属性 | 値 |
|---|---|
| `bot.user` | Bot の `CurrentUser`（接続後） |
| `bot.application_id` | アプリの ID（起動後） |
| `bot.latency` | シャードのハートビート往復時間の平均（秒）、または `None` |
| `bot.is_running` | `start()` または `open()` が Bot を実行していて、まだ `close()` で終了していないかどうか |
| `bot.is_ready` | 最後の `start()` 以降に `Started` が送られたかどうか |
| `bot.rest`、`bot.http` | [REST API](rest.md)（起動後） |
| `bot.gateway` | [ゲートウェイのシャード](gateway.md#raw-gateway-commands)（接続後） |
| `bot.cache` | [キャッシュ](cache.md) |
| `bot.voice` | [ボイス接続](../voice/index.md) |
| `bot.auto_defer` | 既定の[自動遅延応答](responses.md#deferring)までの時間。いつでも変更できます |

`bot.rest`、`bot.http`、`bot.gateway` は、`start()` の前には `StateError` を送出します。

## ロギング { #logging }

bostie は標準の `logging` モジュールに、次の名前でログを出力します。

| ロガー | 報告する内容 |
|---|---|
| `bostie.bot` | 失敗したハンドラーや応答しなかったハンドラー、リスナーが必要とするインテント、処理されなかったインタラクション |
| `bostie.gateway` | 接続、切断、再開（resume）、バックプレッシャー、無効なリクエスト |
| `bostie.rest` | Rust の HTTP クライアントからのレート制限とリトライ |
| `bostie.sync` | 各コマンド同期で変更された内容 |
| `bostie.context` | 編集時のエフェメラルフラグのように、適用できなかった応答 |
| `bostie.events` | イベントに合わなかったペイロード（イベント名ごとに 1 回） |
| `bostie.voice` | ボイス接続、DAVE、シンク、レシーバー、プレイヤー |
| `bostie.bridge` | Python と Rust コアの間の連携 |

Rust コアも同じロガーを通してログを出力し、Bot の起動時にそのレベルを読み込みます。ゲートウェイを詳しく見るには、`run()` の前に `logging.getLogger("bostie.gateway").setLevel(logging.DEBUG)` を設定してください。

## bostie の接続先を変える { #pointing-bostie-elsewhere }

`Bot(api_url=...)` を指定すると、REST リクエストを別のベース URL に送ります。たとえば、複数のプロセスで共有するレート制限プロキシや、テストでの[フェイクの Discord](testing.md) です。省略した場合、bostie は Bot の起動時に `BOSTIE_API_URL` を読み込み、それも未設定なら `https://discord.com/api/v10` を使います。レート制限プロキシの背後では `http_settings=HTTPSettings(rate_limiter=False)` を渡し、待機をプロキシに任せてください。その他の HTTP の設定は [REST ガイド](rest.md#rate-limits-retries-and-timeouts)で説明しています。

## 1 つのプロセスで複数の Bot を動かす { #several-bots-in-one-process }

各 `Bot` は、Rust コアへの独自の接続、独自のキャッシュ、独自の HTTP クライアントを持ちます。そのため 1 つのプロセスで、1 つのイベントループ上に複数の Bot を動かし、それぞれを個別に起動・終了できます。

## 状態を保持する { #keeping-state }

Bot は普通のオブジェクトです。属性を持たせるには `Bot` をサブクラス化します。[ライフサイクルのサンプル](../examples.md#lifecycle)では、`Starting` リスナーで開いたデータベース接続を `Stopped` リスナーで閉じています。`Bot` のすべてのオプションは、[Bot のオプションのリファレンス](../reference/bot-options.md)に一覧があります。
