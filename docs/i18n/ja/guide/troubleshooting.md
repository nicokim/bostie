---
source: e7704fd7192c52cc55342cad633590bd6f185904
description: "bostie のよくある問題をログから解決します。「アプリケーションが応答しませんでした」、届かないイベント、ゲートウェイと REST の警告、ボイスの問題、バグの報告を扱います。"
---

# トラブルシューティング

![疑問符が電球に変わるまで考える Bostie](../assets/mascot/bostie-thinking.webp){ align=right width="180" loading=lazy }

bostie は、検出できる間違いごとに警告をログに出力します。このページでは、それらを目に見える症状ごとに、探すべきログ行と対処法とあわせて一覧にしています。

## コマンドに「アプリケーションが応答しませんでした」と表示される { #a-command-says-the-application-did-not-respond }

| ログ行 | 原因 | 対処法 |
|---|---|---|
| `nothing handles interaction ... (command 'name')` | 実行中の Bot が定義していないコマンドが Discord に残っています。`auto_sync=False` で名前を変えた後によく起こります。または、コマンドが別のプロセスにあります | コマンドを同期します（[コマンドの登録](registration.md)） |
| `nothing handles interaction ... (custom_id '...')` | カスタム ID に一致する `on_component` または `on_modal` のテンプレートがありません | 送っている ID とテンプレートを照らし合わせます。ID は `route.id(...)` で組み立てます |
| `... returned without answering` | ハンドラーが `respond`、`defer`、`modal` のいずれも呼ばずに終了しました | 早期 return を含め、すべての経路で応答します |
| `... returned after deferring without answering` | 遅延応答した後に一度も応答しなかったため、トークンが失効するまで「考え中…」が表示されたままになります | 時間のかかる処理の後に `respond` を呼びます |
| `interaction ... reached its handler ... s after it was received; something is blocking the event loop` | ハンドラーやリスナー内のブロッキングなコードが、Discord の 3 秒の期限を過ぎてもイベントループを占有していました | 処理を `asyncio.to_thread` に移すか、`auto_defer` を小さくします |
| `interaction ... was replayed ... s after it was created; its token is dead` | インタラクションが再開したセッションのリプレイで届いたため、応答するには遅すぎました | 対処は不要です。ユーザーはもう一度実行できます |

ハンドラー内で処理されなかった例外はトレースバックとともにログに出力され、ユーザーには「Something went wrong.」と表示されます。もっと適切な応答を返すには[エラーフック](checks.md#error-hooks)を追加します。

## イベントが届かない { #events-do-not-arrive }

- `... listens to EVENT, which needs the INTENT intent; add it to Bot(intents=...)`：Bot がそのインテントを要求していません。インテントを追加し、特権インテントの場合は Developer Portal でも有効にします。
- `start()` が `PrivilegedIntentsRequired` を送出する：ポータルで許可されていない特権インテントを Bot が要求しました。**Bot** → **Privileged Gateway Intents** で有効にするか、`intents` から外します。
- サーバー内で `message.content` が空になる：`MESSAGE_CONTENT` インテントがありません。
- `EVENT has an unexpected shape (...); typed listeners skip it`：イベントクラスに合わないペイロードを Discord が送ってきました。このログはイベント名ごとに 1 回だけ出力されます。`RawEvent` のリスナーには引き続き届きます。このログ行をぜひ報告してください。
- 更新イベントで `old` が `None` になる：キャッシュにそのオブジェクトがありませんでした。キャッシュが保持するものは[キャッシュ](cache.md#what-it-keeps)を参照してください。

## ゲートウェイ { #the-gateway }

- `shard N paused reading: ... bytes of events are waiting because the event loop is not keeping up`：何かがイベントループをブロックしています。[バックプレッシャー](gateway.md#backpressure)を参照してください。
- `close code N (...) means Discord refused what bostie sent; this is likely a bostie bug, please report it`：コード 4001、4002、4005、4008 は、bostie が送ったものを Discord が拒否したことを意味します。
- `the members of guild N stopped arriving; the cache lacks some`：キャッシュのためのメンバー要求が、チャンクごとの 30 秒を超えてタイムアウトしました。
- `start()` が `InvalidToken` を送出する：トークンが間違っているか、リセットされています。**Bot** ページからもう一度コピーしてください。

## REST { #rest }

- `N invalid requests (401, 403, 429) in the last 10 minutes; Discord bans the IP at 10 000`：Bot が、Discord に拒否されるリクエストを送り続けています。原因は多くの場合、権限の不足か、無効になったウェブフックです。失敗している呼び出しは、それが送出する例外から特定してください。
- リクエストが Discord に届かないまま `RateLimited` が送出される：そのルートの制限が `max_rate_limit_wait` より長く続いています。[`HTTPSettings`](rest.md#rate-limits-retries-and-timeouts) でこの値を大きくするか、後で再試行してください。

## 応答 { #answers }

- `ephemeral=... is ignored because the message being edited is ...`：一度送信したメッセージを、エフェメラルと公開の間で切り替えることはできません。
- `Discord allows 5 follow-ups to an interaction of an app installed only to the user`：ユーザーにのみインストールされたアプリでは、6 回目のフォローアップが失敗します。
- `the error hook ... left interaction ... unanswered; bostie answered 'Something went wrong.'`：フックが応答するようにしてください。

## ボイス { #voice }

- `nobody hears the bot in guild N: it is server muted`、または `it is suppressed on this stage; use vc.stage('request')`：モデレーターが Bot をミュートしたか、Bot がステージのオーディエンスになっています。
- `a moderator deafened the bot in guild N, so Discord sends it no audio`：スピーカーミュートが解除されるまで、レシーバーには何も届きません。
- 理由が `TIMEOUT` の `VoiceConnectError`：Discord が `timeout` 秒以内に参加に応答しませんでした。チャンネルが満員か、Bot に「接続」の権限がない可能性があります。
- `MissingIntent: voice needs Intents.GUILD_VOICE_STATES`：ボイスにはこのインテントが必要です。`Intents.DEFAULT` には含まれています。

## バグの報告 { #reporting-a-bug }

`python -m bostie` は報告に含めるべきバージョンを表示し、ボイスの問題の場合は `bostie.voice.versions()` で libopus と DAVE のバージョンも加えられます。[ロギング](running.md#logging)にあるとおり、Bot を起動する前に該当するロガーを `DEBUG` に設定し、問題の前後のログ行を、トークンを取り除いたうえで添えてください。
