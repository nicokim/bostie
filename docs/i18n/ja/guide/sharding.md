---
source: 77fd407beead25f005f3979ee300e3dbfac29482
description: "Discord Bot を 1 つまたは複数のプロセスでシャーディングし、プロセス間で Identify を調整し、シャードのイベントを追い、再起動後にセッションを再開します。"
---

# シャーディングと再開

![一列に並んで走る 3 匹の Bostie](../assets/mascot/bostie-running.webp){ align=right width="220" loading=lazy }

Discord は、Bot のサーバーをシャードと呼ばれる複数のゲートウェイ接続に分けます。サーバー `g` はシャード `(g >> 22) % shard_count` に属します。2500 を超えるサーバーに参加している Bot はシャーディングが必須です。シャーディングしていない接続を Discord はコード 4011 で閉じ、`start()` は `ShardingRequired` を送出します。

## 1 つのプロセスでのシャード { #shards-in-one-process }

既定値である `Bot(shard_count="auto")` は、使うべきシャードの数を Discord に問い合わせ、それらをすべて実行します。ほかに必要なことはありません。bostie は Discord の Identify の上限内でシャードを開始し、すべてのシャードが同じリスナー、キャッシュ、ハンドラーにイベントを送ります。イベントがどのシャードから届いたかは `event.shard_id` でわかります。

## 複数のプロセスにまたがるシャード { #shards-across-processes }

```python
--8<-- "docs/snippets/sharding/cluster.py"
```

`shard_count=16` で総数を固定し、`shard_ids` でこのプロセスが実行するシャードを選びます。`shard_ids` には明示的な `shard_count` が必要で、その ID は互いに異なり、`range(shard_count)` の範囲内でなければなりません。

Discord は、Bot ごとに同時に行える Identify の数を制限しています。bostie は 1 つのプロセスの中ではその上限を守りますが、プロセスをまたぐ場合は `before_identify` で調整します。bostie は Identify のたびにその前に `before_identify(shard_id)` を await するので、プロセス間で共有するロックの空きをそこで待つことができます。この例では、`identify_lock` がその共有ロックの代わりで、Discord が同じバケットの Identify の間に求める 5 秒間保持されます。フックが例外を送出した場合、bostie はエラーをログに出力したうえで、そのまま Identify を行います。

## シャードのイベント { #shard-events }

| イベント | フィールド | 送られるとき |
|---|---|---|
| `ShardReady` | `shard_id` | シャードの新しいセッションの準備ができ、そのサーバーが届いたとき |
| `ShardDisconnect` | `shard_id`、`code`、`reason`、`resuming` | 接続が切れたとき。`resuming` の場合、シャードはセッションを再開し、取りこぼしたイベントを再送させます。そうでなければ新しいセッションを開始します |
| `ShardStop` | `shard_id`、`code`、`reason` | `close()` が実行されたか Discord が終了させたために、シャードが完全に停止したとき |
| `ShardBackpressure` | `shard_id`、`queued_bytes` | イベントループが追いつかないために、シャードが読み取りを一時停止したとき |

再接続と再開について、あなたがすることは何もありません。`bot.latency` はシャード全体のハートビート往復時間の平均で、`bot.gateway.shard(shard_id).latency` は 1 つのシャードの往復時間です。

## 再起動後に再開する { #resuming-after-a-restart }

通常、再起動するとすべてのシャードで新しいセッションが始まり、その間の数分間のイベントは失われます。`Bot(session_file="sessions.json")` を指定すると、`close()` はセッションを開いたまま保持し、ファイルに書き出します。次の `start()` はそのファイルを読み込んで削除し、現在の `shard_count` とシャード ID に一致する保存済みのセッションをそれぞれ再開します。そのため Discord は、Bot が停止していた間に起きたイベントを再送します。読み込めないファイルはログに記録されて無視され、保存されたセッションのないシャードは Identify を行います。

`bot.run()` は Ctrl+C と SIGTERM で終了するので、プロセスマネージャーやデプロイによる再起動ではセッションが保存されます。クラッシュした場合は保存されず、次の起動では Identify を行います。作成から 5 秒以上経ってから再送されたインタラクションは、そのトークンではもう時間内に応答できないため、警告を出してスキップされます。
