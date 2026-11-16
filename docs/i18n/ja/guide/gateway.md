---
source: 78a0e5466c6f1542e685629c22fbbdc82ec6dcde
description: "特権インテントを含むゲートウェイインテント、Bot のプレゼンスとアクティビティの設定、サーバーメンバーの要求、生のゲートウェイコマンド、バックプレッシャーを解説します。"
---

# インテント、プレゼンス、ゲートウェイ

ゲートウェイは WebSocket で、Discord はこれを通じてイベントを送り、Bot はプレゼンスやリクエストを送ります。bostie の Rust コアは、ハートビート、再開（resume）、再接続、圧縮、送信のレート制限を処理して、接続を維持します。このページでは、Bot がゲートウェイに何を求めるかを説明します。

## インテント { #intents }

Discord は、イベントのインテントを要求した Bot にだけそのイベントを送ります。`Bot(intents=...)` は `Intents` フラグを受け取ります。省略すると `Intents.DEFAULT` で、3 つの特権インテントを除くすべてのインテントです。

| 特権インテント | 必要になるもの |
|---|---|
| `GUILD_MEMBERS` | メンバーの参加・更新・退出のイベント、キャッシュ内のすべてのメンバー、全メンバーの要求 |
| `GUILD_PRESENCES` | プレゼンスの更新と、キャッシュ内のプレゼンス |
| `MESSAGE_CONTENT` | サーバー内のメッセージの内容、埋め込み、添付ファイル、コンポーネント。ただし Bot をメンションしたメッセージは除きます。DM ではインテントは不要です |

特権インテントは、Developer Portal の **Bot** ページでも有効にする必要があります。有効にしていないと、Discord はコード 4014 で接続を閉じ、`start()` は `PrivilegedIntentsRequired` を送出します。そのメッセージには、どこで有効にすればよいかが書かれています。インテントは `|` で追加します（`Intents.DEFAULT | Intents.MESSAGE_CONTENT`）。リスナーが Bot にないインテントを必要とする場合、bostie は警告を出し、オプションやリクエストがインテントを必要とする場合は `MissingIntent` を送出します。

ボイスには `GUILD_VOICE_STATES` が必要で、これは `DEFAULT` に含まれています。

```python
--8<-- "docs/snippets/gateway/intent_presets.py"
```

個々のインテントと並んで、3 つのプリセットがあります。`Intents.DEFAULT`（別名 `Intents.UNPRIVILEGED`）は特権インテント以外のすべてのインテント、`Intents.PRIVILEGED` は 3 つの特権インテント、`Intents.ALL` はその両方です。上の例のように、`& ~` でプリセットからインテントを取り除けます。

## プレゼンス { #presence }

```python
--8<-- "docs/snippets/gateway/presence_and_requests.py"
```

`Bot(status=..., activities=[...], afk=False, idle_since=None)` は、Bot が接続したときに表示する内容を設定します。`await bot.update_presence(...)` は、すべてのシャードと、後から接続するシャードでそれを変更します。省略したオプションは値を保持します。

`status` は `Status` で、`ONLINE`、`IDLE`、`DND`、`INVISIBLE` のいずれかです。アクティビティは `bostie.activity` で作ります。

| ビルダー | 表示 |
|---|---|
| `activity.playing(name)` | *name* をプレイ中 |
| `activity.listening(name)` | *name* を再生中 |
| `activity.watching(name)` | *name* を視聴中 |
| `activity.competing(name)` | *name* に参戦中 |
| `activity.streaming(name, url)` | *name* を配信中（Discord が対応している配信サービスの場合） |
| `activity.custom(text)` | *text* を表示するカスタムステータス |

`custom` 以外はどれも、2 行目のテキストである `state=` を受け取り、`activity.ActivityDict` を返します。

## リクエスト { #requests }

一部のデータは、リクエストへの応答としてゲートウェイ経由でしか得られません。

- `await bot.request_members(guild_id, query="ab", limit=10)` は、ユーザー名またはニックネームが `query` で始まるメンバーを最大 `limit` 人（0 から 100）返します。代わりに `user_ids=[...]` を指定すると、ID で最大 100 人のメンバーを要求します。どちらも指定しないと全メンバーを要求し、これには `GUILD_MEMBERS` が必要です。`presences=True` はプレゼンスも加え、これには `GUILD_PRESENCES` が必要です。応答は `RequestedMembers` で、`members`、`presences`、そして Discord が見つけられなかった ID である `not_found` を持ちます。Discord はこのリクエストをレート制限しており、拒否されると `retry_after` を持つ `MemberRequestRateLimited` が送出されます。
- `await bot.request_soundboard_sounds(guild_id)` は、サーバーのサウンドボードのサウンドを返します。
- `await bot.request_channel_info(guild_id)` は、サーバーのチャンネルのボイスステータスとボイスの開始時刻を返します。`fields=["status"]` を指定すると、要求する内容を減らせます。

どれも応答の各部分を最大 30 秒（メンバーの場合は `timeout=`）待ち、それを過ぎると `TimeoutError` を送出します。新しいセッションを開始中のシャードへのリクエストは `StateError` を送出します。準備ができてからリトライしてください。

## 生のゲートウェイコマンド { #raw-gateway-commands }

`bot.gateway.send(op, d)` は、任意のゲートウェイコマンドを送ります。オペコード 3（プレゼンス）はすべてのシャードに送られます。`guild_id` を持つペイロードはそのサーバーのシャードに、`guild_ids` を持つペイロードは各シャードにそれぞれの担当分の ID とともに送られ、それ以外はシャード 0 に送られます。Discord の応答はイベントとして届き、しかも何かが待ち受けているイベント名に限られます。そのため、生のコマンドは `listen_raw` リスナーや `wait_for` と組み合わせてください。

`bot.gateway` からは、ほかにも次のものが得られます。このプロセスが動かしているシャードを表す `shard_ids`、Discord があるサーバーのイベントを送るシャードを返す `shard_for(guild_id)`、独自の `send`、`reconnect`、`latency` を持つ `shard(shard_id)`、すべての接続を切断して新しい接続でセッションを再開する `reconnect()`、そしてハートビート往復時間の平均である `latency` です。このプロセスが動かしていないシャードへのコマンドは `ShardNotRunning` を送出し、ほかの多くのコマンドの後ろでキューに入りきらないコマンドは `GatewayQueueFull` を送出します。

## バックプレッシャー { #backpressure }

Rust コアは、Discord が送ってくる速さでイベントを読み取り、Python に渡します。イベントループが追いつかなくなると、シャードは Python が追いつくまで読み取りを止め、警告をログに出力し、待機中のバイト数を持つ `ShardBackpressure` を送ります。原因は、ハンドラーやリスナーの中での長いブロッキング処理です。そのような処理は `asyncio.to_thread` でスレッドに移してください。
