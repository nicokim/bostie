---
source: efdcb485cd6bec9ca515ad0f0311945137caa315
description: "Bot なしで、非同期または通常の Python コードから Discord のウェブフック URL でメッセージを投稿、編集、削除し、Bot でウェブフックを作成、管理します。"
---

# ウェブフック

ウェブフックは、独自の名前とアバターで 1 つのチャンネルにメッセージを投稿します。ウェブフックの URL には ID とトークンが含まれており、投稿に必要なのはそのトークンだけです。そのため、デプロイスクリプトや監視ジョブは Bot なしでメッセージを送れます。

```python
--8<-- "docs/snippets/webhooks/deploy_notice.py"
```

`WebhookClient(url)` はウェブフックの URL から ID とトークンを解析し、それ以外の文字列には `ValueError` を送出します。HTTP クライアントを開いて閉じる `async with` の中で使ってください。`SyncWebhookClient(url)` は、イベントループのないコード向けに同じメソッドを持ちます。呼び出しのたびに専用のイベントループを動かします。

| メソッド | 動作 |
|---|---|
| `execute(content, **parts)` | メッセージを投稿し、Discord が保存した時点でそれを返します |
| `edit_message(message_id, **parts)` | ウェブフックが送ったメッセージを編集します。省いたパーツはそのまま残り、`None` を渡すと消去されます |
| `fetch_message(message_id)`、`delete_message(message_id)` | メッセージを 1 件取得または削除します |
| `fetch()`、`edit(name=..., avatar=...)`、`delete()` | ウェブフック自体を操作します |
| `execute_slack(payload)`、`execute_github(payload)` | Discord の Slack 互換および GitHub 互換のエンドポイントが受け付ける形式のペイロードを投稿します |

`execute` は[メッセージのパーツ](messages.md)に加えて、次の引数を受け取ります。

- `username` と `avatar_url`。このメッセージに限り、ウェブフック自身の名前とアバターの代わりに表示されます。
- `thread_id`。チャンネル内のスレッドに投稿します。メッセージ操作のメソッドもこの引数を受け取ります。
- `thread_name`。フォーラムチャンネルやメディアチャンネルでスレッドを開始します。`applied_tags` と組み合わせて使います。
- `with_components=True`。どのアプリにも所有されていないウェブフックでコンポーネントを使う場合に必要です。

クライアントは Bot と同じく `api_url`、`http_settings`、`allowed_mentions` を受け取ります。既定では、ウェブフックはユーザーとロールをメンションで通知できますが、`@everyone` はできません。

Discord が 404 Unknown Webhook で応答した場合、そのウェブフックは削除されています。bostie はそれ以降、そのウェブフックへのリクエストをすべて送信せずに同じ 404 で応答するので、削除済みのウェブフックが[無効なリクエストの上限](rest.md#rate-limits-retries-and-timeouts)に数えられることはありません。

## Bot でウェブフックを管理する { #managing-webhooks-with-a-bot }

`bot.rest.webhooks` は、チャンネルやサーバーのウェブフックを作成・一覧表示し（「ウェブフックの管理」権限が必要です）、トークンを使うメソッドも備えています。

- `create(channel_id, name=..., avatar=File(...))` は `Webhook` を返します。その `url` を `WebhookClient` に渡せます。
- `list(channel_id)`、`list_guild(guild_id)`、`fetch(webhook_id)`、`edit(webhook_id, ...)`、`delete(webhook_id)` は Bot のトークンで動作します。
- `execute(webhook_id, token, ...)` と、そのほかの `_with_token` のメソッドやメッセージ操作のメソッドは、`WebhookClient` が内部で呼び出すものです。

`WebhooksUpdate` イベントは、チャンネルのウェブフックが変更されたことを知らせます。ウェブフックが送ったメッセージは `WebhookMessage` として届き、その `author` は `PartialUser` で、`webhook_id` が設定されています。
