---
source: 029b6094446c707896abd078da818c4746bbebec
---

# チェック、フック、エラー

![腕を組んで怒っている Bostie](../assets/mascot/bostie-angry.webp){ align=right width="180" loading=lazy }

チェックは、ハンドラーを実行してよいかどうかを判断します。フックは、ハンドラーの前後でコードを実行します。エラーフックは、何かが例外を送出したときに応答します。

## チェック { #checks }

```python
--8<-- "docs/snippets/checks/command_checks.py"
```

チェックは、コンテキストを受け取って `bool` を返す（または await して返す）関数です。`False` を返すと `CheckFailure` で実行を拒否します。チェックが独自の `CheckFailure` を送出することもでき、そのメッセージがユーザーに伝えられます。チェックは、コマンド、グループ、サブコマンド、ユーザーコマンド、メッセージコマンド、エントリーポイントコマンド、コンポーネントやモーダルのルートの `checks=[...]` 設定に指定します。`@router.check` は、ルーターのすべてのハンドラーに対するチェックを登録します。Bot もルーターの一種です。

最初に Bot のチェックが実行され、次に外側から順に各ルーターのチェック、次に上位から順にグループのチェック、最後にコマンド自身のチェックが実行されます。最初に拒否された時点で実行は止まります。オートコンプリートではチェックは実行されません。

よく使うチェックは `bostie.checks` にあります。

| チェック | 拒否時の例外 | 拒否される条件 |
|---|---|---|
| `has_permissions(permissions)` | `MissingPermissions` | ユーザーがそのチャンネルで `permissions` のいずれかを持っていない、または DM にいる |
| `bot_has_permissions(permissions)` | `BotMissingPermissions` | Bot がそのチャンネルでそれらのいずれかを持っていない |
| `Cooldown(rate, seconds, per="user")` | `CooldownActive` | そのキーが直近 `seconds` 秒以内にすでに `rate` 回使っている |
| `MaxConcurrency(number, per="global")` | `MaxConcurrencyReached` | そのキーの実行がまだ `number` 件続いている |
| `has_role(role)` | `MissingRole` | ユーザーが ID または名前で指定したロールを持っていない、または DM にいる |
| `has_any_role(*roles)` | `MissingRole` | ユーザーがどのロールも持っていない |
| `is_owner` | `NotOwner` | ユーザーが `bot.owner_ids` に含まれていない |

`MaxConcurrency` のように `release(ctx)` メソッドも持つチェックオブジェクト（`checks.ReleasingCheck`）は、ハンドラーがどのように終わっても、終了時に解放されます。

`per` には `"user"`、`"guild"`（DM ではユーザー）、`"channel"`、`"global"`、またはコンテキストを受け取って任意のハッシュ可能なキーを返す関数を指定します。権限のチェックは Discord がインタラクションと一緒に送る権限を読むので、キャッシュもリクエストも必要ありません。これらのエラーは不足している `Permissions` を `missing` に持ち、`CooldownActive` は秒単位の `retry_after` を持ちます。

## ロールとオーナー { #roles-and-owners }

```python
--8<-- "docs/snippets/checks/staff_checks.py"
```

`has_role` と `has_any_role` は、ロールの ID または名前を受け取ります。ID は Discord がインタラクションと一緒に送るロールと照合されるので、キャッシュは必要ありません。名前はキャッシュされたサーバーのロールから検索されるので、キャッシュを使わない Bot や [HTTP のみのアプリ](http-interactions.md)では、ロールを ID で指定してください。`MissingRole` は要求された `roles` を持ち、そのメッセージは ID で指定されたロールをメンションするので、Discord ではロール名として表示されます。

`is_owner` は、括弧を付けずにそのままチェックとして使います。通過させるユーザーは `bot.owner_ids` に入っています。これは `Bot(...)` に渡した `owner_ids`、指定がなければ Bot の起動時にアプリケーションから読み込んだアプリのオーナーです。チームに属するアプリの場合は、チームのオーナーと、招待を承認した管理者と開発者になります。読み取り専用のメンバーは含まれません。

## ユーザーに伝えられる内容 { #what-the-user-is-told }

`CheckFailure` を処理するエラーフックがない場合、bostie はそのメッセージでエフェメラルに応答します。メッセージは「You need the Manage Messages permission.」、「The bot needs the Manage Messages permission.」、「in 42 seconds」のような相対タイムスタンプ付きの「Try again」、「This is already running; try again once it is done.」、「You need the Moderator role.」、「Only the bot's owners can use this.」、そして単に `False` が返された場合は「You cannot use this.」です。

## 前後のフック { #hooks-before-and-after }

```python
--8<-- "docs/snippets/checks/error_hooks.py"
```

`@router.before_invoke` は、チェックを通過した後、ハンドラーの前に実行される `async def hook(ctx)` を登録します。`@router.after_invoke` は、ハンドラーの後に実行されるフックを登録し、ハンドラーが例外を送出した場合も実行されます。これらはルーターのすべてのコマンド、コンポーネントのルート、モーダルのルートで実行されるので、コンポーネントを受け取る可能性のあるフックでは `Context` だと決めつけないでください。Bot の前フックは最初に、後フックは最後に実行されます。後フックの例外はログに記録され、応答は変わりません。

## エラーフック { #error-hooks }

チェック、コンバーター、前フック、ハンドラーから送出された例外は、エラーフックに渡されます。`@x.on_error` はすべての `Exception` に対する `async def hook(ctx, error)` を登録し、`@x.on_error(NotFound, ValueError)` はそれらの型とそのサブクラスに対するフックを登録します。`x` はコマンド、グループ、コンポーネントやモーダルのルート、ルーター、または Bot で、それぞれ例外の型ごとに 1 つのフックを持てます。

bostie は次の順にフックを探し、最初に見つかったものがエラーを処理します。

1. コマンド、次にそのグループ、最上位のグループ。
2. コマンドが属するルーター、次にそのルーターを含む各ルーター、Bot まで。

同じ場所の中では、エラーに最も近いクラスのフックが優先されます。たとえば `NotFound` のフックは `HTTPException` のフックより優先されます。同じエラーを送出したフックは、上の `slow_down` のように、この順序で次のフックにエラーを渡します。別のエラーを送出したフックの場合、そのエラーはログに記録されます。

コンテキストの型は `ErrorContext` で、`Context`、`ComponentContext`、`ModalContext`、`AutocompleteContext` のいずれかです。オートコンプリートのコンテキストは `respond` できないので、上の例のように確認してください。

フックが応答せずに戻った場合、bostie は警告をログに記録し、「Something went wrong.」と応答します。エラーを処理するフックがない場合、bostie は `CheckFailure` にはそのメッセージで応答し、それ以外はトレースバック付きでログに記録してから「Something went wrong.」と応答します。ハンドラーがすでに応答していた場合は何も応答せず、オートコンプリートには候補の空のリストが返されます。

## ハンドラー外のエラー { #errors-outside-handlers }

リスナーとタスクには、応答する相手のユーザーがいません。リスナー内の例外は `ListenerError` イベントとして、タスク内の例外は `TaskError` として送られます。これらのリスナーがなければ、bostie がログに記録します。[イベント](events.md#when-a-listener-raises)と[バックグラウンドタスク](tasks.md)を参照してください。

## 例外の型 { #exception-types }

Discord、ネットワーク、Bot の状態に関して bostie が送出する例外は、すべて `BostieError` です。引数が不正な場合は、代わりに `ValueError` または `TypeError` を送出します。一部のエラーは標準の例外でもあります。`StateError` は `RuntimeError`、`MissingIntent` は `ValueError`、`StartupError` は `OSError`、`CommandDefinitionError` は `TypeError` です。コマンドとインタラクションに関するエラーは基底クラス `CommandError` を、ボイスのエラーは `VoiceError` を共有します。

| 例外 | 送出される条件 |
|---|---|
| `HTTPException` | REST リクエストがエラーステータスで終わった。`status`、`code`（`JSONErrorCode`）、`message`、`errors`、生の `body` を持つ |
| `BadRequest`、`Forbidden`、`NotFound`、`ServerError` | 400、403、404、5xx |
| `RateLimited` | 429、または `max_rate_limit_wait` より長く待つことになるリクエストを bostie が止めた。`retry_after`、`is_global` を持つ |
| `InvalidToken` | REST またはゲートウェイで、Discord がトークンを拒否した |
| `TransportError` | Discord が応答する前にリクエストが失敗した |
| `UnexpectedResponse` | Discord はリクエストを受け付けたが、そのレスポンスをデコードできない。`raw` にレスポンスが入っている |
| `RequestBudgetExhausted` | 直近の無効なリクエストが多すぎるため、IP を守るために bostie がリクエストを拒否した |
| `GatewayClosed`、`PrivilegedIntentsRequired`、`ShardingRequired` | Discord がゲートウェイを恒久的に閉じた。後の 2 つはコード 4014 と 4011 の場合 |
| `ShardNotRunning`、`GatewayQueueFull` | このプロセスが実行していないシャードに対するゲートウェイコマンド、または待機中のコマンドが多すぎる |
| `StateError` | `start()` より前の `bot.rest` など、オブジェクトの状態では許されない呼び出し |
| `MissingIntent` | Bot が要求していないインテントを必要とする機能を使った |
| `MemberRequestRateLimited` | Discord がメンバーのリクエストを拒否した。`retry_after` を持つ |
| `StartupError` | bostie が必要なシステムリソースを用意できなかった |
| `CommandDefinitionError` | コマンド、グループ、オプションが Discord のルールに違反している。デコレーターが送出する |
| `CommandSyncError` | 同期の際に Discord がコマンドを拒否した |
| `OptionConversionError` | オプションまたはターゲットを型ヒントに変換できなかった |
| `InteractionResponded`、`InteractionNotAnswered`、`InteractionExpired` | インタラクションの状態では許されない応答の呼び出し |
| `CheckFailure` とそのサブクラス | チェックが実行を拒否した |

ボイスのエラーは[ボイスのページ](../voice/index.md#errors)にあります。[エラーのリファレンス](../reference/api/errors.md)には、すべてのクラスとその属性が載っています。
