---
source: d24ff7127c79eec4345b1d42ca0265273465a567
description: "ゲートウェイの代わりに ASGI アプリで Discord のインタラクションとウェブフックイベントを HTTP で受け取り、署名を検証し、独自の Web サーバーでも動かします。"
---

# HTTP インタラクションとウェブフックイベント

Discord は、インタラクションをゲートウェイの代わりにアプリの URL へ送ることができます。また、一部のイベント（ウェブフックイベント）は URL にしか送りません。`ASGIApp` は、その両方を同じ Bot で処理します。コマンド、ルート、リスナーは変更不要です。

```python
--8<-- "docs/snippets/http/http_bot.py"
```

`bostie.ASGIApp(bot)` は ASGI アプリケーションです。任意の ASGI サーバーで、HTTPS の背後で `app` を公開してください。次に Developer Portal で、アプリの **Interactions Endpoint URL** と、ウェブフックイベントを使う場合は **Webhook Events URL** を、受け取りたいイベントの種類とともに、アプリが待ち受けている場所に設定します。Discord はエンドポイントを保存する時点で、PING と、署名が誤ったリクエストを送って確認します。`ASGIApp` はその両方に Discord が期待するとおりに応答します。

## 何をするか { #what-it-does }

サーバーが ASGI の lifespan を通じて起動すると、Bot がすでにゲートウェイで動いている場合を除き、アプリは `bot.open()` を呼び出します。これは次の処理を行います。

1. Bot の HTTP クライアントを開きます。
2. アプリケーション（その ID と、Discord のリクエストに署名する公開鍵を得るため）と、Bot のユーザーを取得します。
3. `Starting` を送り、コマンドを同期し、[バックグラウンドタスク](tasks.md)を開始します。

この段階で失敗すると、サーバーの起動はそのエラーで中止されます。サーバーが終了するとき、アプリは自分が起動した Bot であれば閉じます。

各リクエストは、何よりも先に検査されます。受け付けるのは `POST` だけで（それ以外は 405）、本文の上限は 4 MiB（超えると 413）で、`X-Signature-Ed25519` が公開鍵で検証できないリクエストには 401 を返します。そのうえで、

- インタラクションの PING には、Discord が期待する PONG を返します。
- それ以外のインタラクションには 202 で応答し、Bot に渡します。Bot はゲートウェイの場合と同じようにコマンド、ルート、モーダルのハンドラーを実行し、ハンドラーは `ctx.respond` などを使って Discord の REST API 経由で応答します。[自動の遅延応答](responses.md#deferring)も同じように動作します。
- ウェブフックイベントは Bot のリスナーに届き、204 を返します。Discord が Webhook Events URL に送る PING にも 204 を返します。

`ASGIApp(bot, token=..., public_key=...)` はトークン（省略時は `BOSTIE_TOKEN`）と公開鍵（省略時はアプリケーションが報告する公開鍵）を受け取ります。

## ゲートウェイの有無 { #with-or-without-the-gateway }

アプリを単独で公開した場合、ゲートウェイ接続はありません。ゲートウェイイベントも、キャッシュの更新も、ボイスもありません。コマンド、コンポーネント、モーダル、REST、ウェブフックイベントは動作します。両方を使うには、同じイベントループで Bot をゲートウェイでも起動します。するとアプリは動作中の Bot を使うようになり、インタラクションは HTTP で届き、ゲートウェイは引き続きイベントを配信します。

## ウェブフックイベント { #webhook-events }

| イベント | 送られるタイミング |
|---|---|
| `ApplicationAuthorized` | ユーザーがアプリをサーバーまたは自分のアカウントに追加したとき |
| `ApplicationDeauthorized` | ユーザーがアプリを削除したとき、またはアプリの OAuth2 トークンが失効したとき |
| `EntitlementCreate`、`EntitlementUpdate`、`EntitlementDelete` | エンタイトルメントが変更されたとき |
| `QuestUserEnrollment` | ユーザーがクエストに追加されたとき。Discord はこれを文書化していますが、まだアプリには送っていません |
| `LobbyMessageCreate`、`LobbyMessageUpdate`、`LobbyMessageDelete` | Social SDK のロビーでメッセージが送信、編集、削除されたとき |
| `GameDirectMessageCreate`、`GameDirectMessageUpdate`、`GameDirectMessageDelete` | Social SDK のセッション内のダイレクトメッセージが変更されたとき |

ゲートウェイイベントと同じく `@bot.listen` で受け取ります。bostie がモデル化していない種類のウェブフックイベントは `RawEvent` として届きます。

## 独自の Web サーバーで使う { #in-your-own-web-server }

`ASGIApp` が使っている 2 つの部品は公開されているので、すでに動かしている Web フレームワークでも使えます。

```python
--8<-- "docs/snippets/http/own_server.py"
```

`verify_signature(public_key, signature, timestamp, body)` は、リクエストの `X-Signature-Ed25519` ヘッダーと `X-Signature-Timestamp` ヘッダーを、アプリの 16 進数の公開鍵で検証します。`False` が返ったら 401 で応答してください。`decode_webhook_event(body)` は、Webhook Events のリクエストから型付きのイベントを返します。bostie がモデル化していない種類なら `RawEvent` を、PING なら `None` を返します。独自のサーバーでインタラクションを扱う場合は、[HTTP インタラクションのサンプル](../examples.md#http-interactions)が ASGI での方法を示しています。bostie がサポートしているのはこの方法です。
