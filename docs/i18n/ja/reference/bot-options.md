---
source: 104f50c5749c73fc5952a47fa18309cb11805eb3
---

# Bot のオプション

`Bot(...)` のすべてのオプションです。どれもキーワード専用引数です。各オプションは、ガイドで文脈とあわせて説明しています。

## ゲートウェイと識別 { #gateway-and-identity }

| オプション | 既定値 | 意味 |
|---|---|---|
| `intents` | `Intents.DEFAULT` | 要求する[インテント](../guide/gateway.md#intents)。既定値には 3 つの特権インテントを除くすべてのインテントが含まれます |
| `status` | `Status.ONLINE` | ログイン時の[プレゼンス](../guide/gateway.md#presence)のステータス |
| `activities` | `()` | ログイン時のアクティビティ。`bostie.activity` で組み立てます |
| `afk` | `False` | Bot が自分を離席中として報告するかどうか |
| `idle_since` | `None` | Bot がアイドル状態になった `datetime` |
| `large_threshold` | 50 | 50 から 250。メンバー数がこの値を超えると、Discord はサーバーのペイロードからオフラインのメンバーを省きます |
| `shard_count` | `"auto"` | [シャード](../guide/sharding.md)の数。`"auto"` にすると Discord の推奨値を使います |
| `shard_ids` | `"all"` | このプロセスが動かすシャード。`shard_count` を明示する必要があります |
| `before_identify` | `None` | 各 Identify の前に await される `async def f(shard_id)`。プロセス間の調整に使います |
| `session_file` | `None` | `close()` がセッションを保存し、次の `start()` がそれを[再開](../guide/sharding.md#resuming-after-a-restart)するためのパス |
| `guild_ready_timeout` | 2.0 | 新しいサーバーが届かないまま経過すると、シャードが[準備完了](../guide/cache.md#startup)とみなされるまでの秒数 |

## コマンドとインタラクション { #commands-and-interactions }

| オプション | 既定値 | 意味 |
|---|---|---|
| `auto_sync` | `True` | 起動時、Discord 上のコマンドと異なる場合に[コマンドを同期](../guide/registration.md)します |
| `dev_guilds` | `()` | グローバルではなくコマンドを登録する先のサーバー ID |
| `contexts` | `None` | コマンドやルーターが別に指定しない限り、グローバルコマンドを使える場所 |
| `integration_types` | `None` | コマンドやルーターが別に指定しない限り、グローバルコマンドを持つインストールの種類 |
| `translate` | `None` | [翻訳](../guide/registration.md#translations)または `None` を返す `f(text, locale)` |
| `auto_defer` | 2.5 | 応答のないインタラクションを[遅延応答](../guide/responses.md#deferring)にするまでの秒数。`None` で無効になります |
| `allowed_mentions` | `AllowedMentions(parse=("users", "roles"))` | 既定で[通知を送る](../guide/responses.md#allowed-mentions)ことができるメンション |
| `owner_ids` | `None` | [`is_owner`](../guide/checks.md#roles-and-owners) が通すユーザー。`None` の場合はアプリのオーナーまたはチームを読み込みます |

## キャッシュ { #cache }

| オプション | 既定値 | 意味 |
|---|---|---|
| `cache` | `True` | [キャッシュ](../guide/cache.md)を保持します。`False` の場合は何も保持しません |
| `members` | `False` | すべてのメンバーを保持します。`Intents.GUILD_MEMBERS` が必要です |
| `chunk_members` | `True` | `members=True` のとき、起動時に Discord が省いたメンバーを要求します |
| `presences` | `False` | プレゼンスを保持します。`Intents.GUILD_PRESENCES` が必要です |
| `max_messages` | 0 | 保持する最新メッセージの数 |
| `max_dm_channels` | 0 | 保持する最新の DM チャンネルの数 |

## HTTP、ボイス、終了処理 { #http-voice-and-shutdown }

| オプション | 既定値 | 意味 |
|---|---|---|
| `http_settings` | `HTTPSettings()` | REST リクエストの[レート制限、リトライ、タイムアウト、プロキシ](../guide/rest.md#rate-limits-retries-and-timeouts) |
| `api_url` | `None` | REST のベース URL。指定がなければ `BOSTIE_API_URL`、それもなければ Discord の URL を使います |
| `voice` | `voice.Defaults()` | すべての接続の出発点となる[ボイスの設定](../voice/index.md#settings) |
| `shutdown_grace` | 0.0 | [`close()`](../guide/running.md#the-lifecycle) が実行中のハンドラーをキャンセルする前に与える猶予の秒数 |

`run(token=None, loop_factory=None)` と `start(token=None)` はトークンを受け取り、既定では `BOSTIE_TOKEN` を使います。
