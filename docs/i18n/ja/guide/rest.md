---
source: 9b56e99b0e7750b300986ad6197431b73305caa0
---

# REST API

![ボールに飛びつく Bostie](../assets/mascot/bostie-jumping.webp){ align=right width="180" loading=lazy }

`bot.rest` には、Bot が呼び出せる Discord の REST API のすべての操作に対応するメソッドがあり、操作の対象となるリソースごとにグループ化されています（`bot.rest.messages.create`、`bot.rest.members.add_role`、`bot.rest.guilds.fetch`）。リクエストは bostie の Rust 製 HTTP クライアントを通して送られ、このクライアントが Discord のレート制限を守ります。

```python
--8<-- "docs/snippets/rest/rest_calls.py"
```

## 規約 { #conventions }

- URL に含まれる ID が先頭に来ます。位置専用引数で、URL 内の順番どおりです（`bot.rest.members.add_role(guild_id, user_id, role_id)`）。
- それ以外はすべて Discord のドキュメントどおりの名前のキーワード引数です。操作ごとの `TypedDict` と照合されるので、打ち間違いは型エラーになり、実行時には `TypeError` になります。
- Discord が監査ログに記録する操作は `reason=` を受け取ります。1 〜 512 文字で、サーバーのモデレーターに表示されます。
- 絵文字の `image` やサーバーの `icon` のような画像やサウンドのフィールドは `File` を受け取り、bostie は Discord が期待する data URI として送信します。権限のフィールドは `Permissions` を受け取ります。
- `reactions.create` が追加する絵文字のような絵文字の引数は、Unicode 絵文字、`<:name:id>`、`<a:name:id>`、`name:id` 形式のカスタム絵文字、または `Emoji` か `PartialEmoji` のモデルを受け取ります。
- `fetch` という名前のメソッドは 1 つのオブジェクトを取得し、`list` は複数のオブジェクトを順に返し、`edit` は変更し、`create` は作成し、`delete` は削除します。それ以外の動詞は、`members.add_role` や `guilds.begin_prune` のように、ドキュメントの単語をそのまま使います。
- メソッドはモデルを返します。Discord が内容なしで応答した場合は `None` を返します。メソッドは `bot.cache` を変更しません。ただし `channels.create_dm` は、`max_dm_channels` が許す範囲で DM チャンネルを保持します。キャッシュは、変更の後に届くゲートウェイイベントによって更新されます。

## ページング { #paging }

Discord がページ分割するエンドポイントの `list` メソッドは非同期イテレーターを返し、読み進めるにつれて次のページを取得します。上の `/today` のように `async for message in bot.rest.messages.list(channel_id, limit=500):` と書きます。`limit` を指定しなければ、すべての項目をたどります。`before`、`after`、`around` は別の位置から開始します（指定できるのはどれか 1 つだけです）。これらは ID か `datetime` を受け取り、`datetime` は bostie がその時点の ID に変換します。メッセージは新しい順に返されますが、`after` を指定すると古い順になります。Discord がページ分割しないエンドポイントの `list` メソッドは、タプルを返すコルーチンです。`audit_log.list` は、エントリーが参照するユーザー、ウェブフック、その他のオブジェクトを含む `AuditLogPage` オブジェクトをページ単位で返します。

## メッセージの一括削除 { #purging-messages }

`messages.purge(channel_id, limit=100, check=None)` は直近 `limit` 件のメッセージを新しい順にたどり、`check` が真を返したメッセージを削除します。`check` がなければすべて削除します。`limit=None` を指定するとチャンネル全体をたどり、`before` と `after` は `list` と同じく別の位置から開始します。Discord は作成から 14 日未満のメッセージしか一括削除できないため、14 日未満のメッセージは最大 100 件ずつの一括削除で、それより古いメッセージは 1 件につき 1 リクエストで削除します。スレッドの最初のメッセージのように、Discord が削除を許さない種類のメッセージは飛ばされます。`bulk=False` を指定すると 1 件ずつ削除します。一括削除が存在しない DM ではこの方法になります。戻り値は削除したメッセージで、`reason` は監査ログに記録されます。一括削除と他人のメッセージの削除には「メッセージの管理」権限が必要です。

```python
--8<-- "docs/snippets/rest/purge.py"
```

## 監査ログ { #the-audit-log }

`audit_log.list(guild_id)` は `AuditLogPage` オブジェクトを順に返し、ゲートウェイは新しいエントリーを 1 件ずつ `AuditLogEntryCreate` イベントとして送ります。エントリーの `changes` には、変更されたフィールドごとに 1 つの `AuditLogChange` が入ります。その `new_value` と `old_value` は、変更されたオブジェクトがそのフィールドに与える型を持ちます。ロールの `permissions` なら `Permissions`、メンバーの `communication_disabled_until` なら `datetime`、チャンネルの `permission_overwrites` なら `Overwrite` のタプルです。Discord の例外的な扱いはドキュメントに従います。メンバーのロールに対する `$add` と `$remove` は `AuditLogRole` オブジェクトを持ち、ウェブフックの `avatar_hash` と招待の `channel_id` はそれぞれ `avatar` と ID の型を取り、コマンドの権限の変更は、ロール、ユーザー、チャンネルの ID をキーとして `CommandPermission` を持ちます。bostie が知らないキーや、フィールドが受け付けない形の値は JSON の値のまま保持され、Discord が省いた値は `None` になります。

```python
--8<-- "docs/snippets/rest/audit_log.py"
```

## エラー { #errors }

Discord がエラーステータスで応答したリクエストは `HTTPException` を送出します。400 なら `BadRequest`、403 なら `Forbidden`、404 なら `NotFound`、5xx なら `ServerError` です。例外は `status`、`JSONErrorCode` としての Discord の `code`、その `message`、フィールドごとの `errors`、生の `body` を持ちます。例外のテキストには、フィールドごとのエラーが 1 行ずつ並びます。`UnexpectedResponse` は、Discord がリクエストを受け付けたものの、その応答をデコードできなかったことを意味します。メッセージには変更が反映されたかどうかが書かれ、`error.raw` が応答を保持します。

## レート制限、再試行、タイムアウト { #rate-limits-retries-and-timeouts }

Rust クライアントは、すべてのルートのレート制限のバケットと Discord のグローバル制限を追跡し、バケットに空きができるまでリクエストを保留します。`Bot(http_settings=HTTPSettings(...))` でその挙動を変更できます。

| `HTTPSettings` のフィールド | 既定値 | 意味 |
|---|---|---|
| `global_rate_limit` | 50 | すべてのルートを合わせた 1 秒あたりのリクエスト数。Discord から Bot により高い上限が与えられている場合は引き上げてください |
| `max_rate_limit_wait` | 30.0 | レート制限で待つ最長時間。これより長く待つことになるリクエストは、送信されずに `RateLimited` を送出します |
| `retries` | 2 | 失敗したリクエストを再試行する回数。指数バックオフで、待ち時間は最大 `max_rate_limit_wait` です |
| `connect_timeout` | 10.0 | 接続を確立するまでの秒数 |
| `read_timeout` | 60.0 | 応答を待つ秒数 |
| `rate_limiter` | `True` | `False` にすると即座に送信し、返ってきた 429 の分だけ待ちます。レート制限プロキシの背後で使うためのものです |
| `proxy` | `None` | すべてのリクエスト（`voice.Url` の音声と CDN からのダウンロードを含む）に使う HTTP または HTTPS プロキシの URL。認証情報は `http://user:password@host:3128` のように含めます |

429 は、Discord が示した待ち時間が `max_rate_limit_wait` 以内であれば、その時間の後に再試行されます。500、502、503、504、524 や、途中で切れた接続は、`POST` 以外のすべてのメソッドで `retries` 回再試行されます。`POST` はすでに反映されている可能性があるためです。一度も確立できなかった接続は、すべてのメソッドで再試行されます。

Discord は、10 分間に 10,000 件の無効なリクエスト（401、403、429）を送った IP を BAN します。bostie はこれを数えており、1,000 件、5,000 件、9,000 件の時点で警告をログに出します。9,500 件に達すると、直近 11 分間の件数が 9,000 件を下回るまで、すべてのリクエストを送信せずに `RequestBudgetExhausted` で拒否します。

## 任意のエンドポイント { #any-endpoint }

`bot.http.request(method, route, **params)` は、同じクライアントを通して任意のリクエストを送り、デコードした JSON を返します。応答が空の場合は `None` を返します。ルートはプレースホルダーを含んだまま渡し、キーワード引数でそれを埋めます（`await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=123)`）。`json=`、マルチパートでアップロードする `files=`、名前付きのフォームフィールドを渡す `form=`、form-urlencoded の本文を渡す `urlencoded=`、`query=`、`reason=` を受け取り、同じ例外を送出します。`bot.http.send(...)` は同じ引数を受け取り、応答の生のバイト列を返します。JSON で応答しないエンドポイントに使います。

## ファイルのダウンロード { #downloading-files }

添付ファイルや画像は API の背後ではなく Discord の CDN にあるので、ダウンロードではトークンを送らず、レート制限も消費しません。`bot.http` は同じ Rust クライアントを通して、そのタイムアウト、再試行、`proxy` を使ってそれらを取得します。

| メソッド | 動作 |
|---|---|
| `read(source)` | バイト列を返します |
| `save(source, path)` | `path` のファイルに書き込みます |
| `to_file(source)` | 再アップロード用の `File` を返します。`Attachment` からの場合はファイル名、説明、タイトル、スポイラー、ボイスメッセージの情報を引き継ぎ、URL からの場合はパスの最後の部分がファイル名になります |

`source` は `Attachment` か、[`bostie.cdn`](formatting.md) のヘルパーが作るような URL です。ファイルが存在しない場合は `NotFound` を送出します。

```python
--8<-- "docs/snippets/rest/downloads.py"
```

## Bot なしで使う { #without-a-bot }

REST だけが必要なスクリプトや Web バックエンドでは `RESTClient` を使います。

```python
--8<-- "examples/rest_script.py"
```

`async with RESTClient(token) as client:` で HTTP クライアントを開きます。`client.rest` は `bot.rest` のすべてのグループを持ち、`client.http` は生のクライアントです。トークンの既定値は `BOSTIE_TOKEN` で、クライアントは Bot と同じく `api_url`、`http_settings`、`allowed_mentions` を受け取ります。ユーザーのトークンで API を呼び出す `BearerClient` については [OAuth2 のガイド](oauth2.md)で、トークンをまったく必要としないクライアントについては[ウェブフックのガイド](webhooks.md)で扱います。

## グループ一覧 { #the-groups }

| `bot.rest.` | メソッド |
|---|---|
| `application` | `edit`、`fetch`、`fetch_activity_instance`、`fetch_bot_application`、`list_role_connection_metadata`、`update_role_connection_metadata` |
| `audit_log` | `list` |
| `auto_moderation` | `create`、`delete`、`edit`、`fetch`、`list` |
| `bans` | `bulk_create`、`create`、`fetch`、`list`、`remove` |
| `channels` | `add_recipient`、`create`、`create_dm`、`create_group_dm`、`delete`、`delete_permission`、`edit`、`edit_permissions`、`edit_positions`、`fetch`、`fetch_member_permissions`、`fetch_role_permissions`、`follow`、`list`、`remove_recipient`、`set_voice_status`、`trigger_typing_indicator` |
| `commands` | `bulk_overwrite`、`bulk_overwrite_guild`、`create`、`create_guild`、`delete`、`delete_guild`、`edit`、`edit_guild`、`fetch`、`fetch_guild`、`fetch_permissions`、`list`、`list_guild`、`list_permissions` |
| `emojis` | `create`、`create_application`、`delete`、`delete_application`、`edit`、`edit_application`、`fetch`、`fetch_application`、`list`、`list_application` |
| `gateway` | `fetch`、`fetch_bot` |
| `guilds` | `begin_prune`、`edit`、`edit_incident_actions`、`edit_welcome_screen`、`edit_widget`、`fetch`、`fetch_preview`、`fetch_prune_count`、`fetch_vanity_url`、`fetch_welcome_screen`、`fetch_widget`、`fetch_widget_image`、`fetch_widget_settings`、`leave`、`list` |
| `identities` | `delete`、`fetch_profile`、`list`、`list_by_external_id`、`update_profile` |
| `integrations` | `delete`、`list` |
| `invites` | `add_target_user`、`bulk_add_target_users`、`bulk_delete_target_users`、`create`、`delete`、`fetch`、`fetch_target_users_job_status`、`list`、`list_guild`、`list_target_users`、`remove_target_user`、`update_target_users` |
| `lobbies` | `add_member`、`bulk_update_members`、`create`、`create_invite_for_user`、`delete`、`edit`、`fetch`、`remove_member`、`update_message_moderation_metadata` |
| `members` | `add`、`add_role`、`edit`、`edit_current`、`fetch`、`fetch_permissions`、`list`、`remove`、`remove_role`、`search` |
| `messages` | `bulk_delete`、`create`、`crosspost`、`delete`、`edit`、`fetch`、`list`、`list_pins`、`pin`、`purge`、`search`、`unpin` |
| `monetization` | `consume_entitlement`、`create_test_entitlement`、`delete_test_entitlement`、`fetch_entitlement`、`fetch_subscription`、`list_entitlements`、`list_skus`、`list_subscriptions` |
| `onboarding` | `edit`、`fetch` |
| `partner_sdk` | `create_provisional_token`、`exchange_child_token`、`exchange_external_token`、`unmerge_external_account`、`unmerge_provisional_account`、`update_dm_moderation_metadata` |
| `polls` | `end`、`list_answer_voters` |
| `reactions` | `create`、`delete`、`delete_all`、`delete_all_for_emoji`、`delete_own`、`list` |
| `roles` | `create`、`delete`、`edit`、`edit_positions`、`fetch`、`fetch_member_counts`、`fetch_permissions`、`list` |
| `scheduled_events` | `create`、`delete`、`edit`、`fetch`、`list`、`list_users` |
| `soundboard` | `create`、`delete`、`edit`、`fetch`、`list`、`list_default`、`send` |
| `stage_instances` | `create`、`delete`、`edit`、`fetch` |
| `stickers` | `create`、`delete`、`edit`、`fetch`、`fetch_guild`、`fetch_pack`、`list`、`list_packs` |
| `templates` | `create`、`delete`、`edit`、`fetch`、`list`、`sync` |
| `threads` | `add_member`、`fetch_member`、`join`、`leave`、`list_active`、`list_joined_private_archived`、`list_members`、`list_private_archived`、`list_public_archived`、`remove_member`、`start`、`start_from_message`、`start_in_forum` |
| `users` | `edit_current`、`fetch`、`fetch_current` |
| `voice` | `edit_current_state`、`edit_state`、`fetch_current_state`、`fetch_state`、`list_guild_regions`、`list_regions` |
| `webhooks` | `create`、`delete`、`delete_message`、`delete_with_token`、`edit`、`edit_message`、`edit_with_token`、`execute`、`execute_github`、`execute_slack`、`fetch`、`fetch_message`、`fetch_with_token`、`list`、`list_guild` |

各メソッドの引数と戻り値の型は [REST API のリファレンス](../reference/api/rest.md)に載っています。いくつかの操作は意図的に `bot.http.request` に任せています。Discord が非推奨にした、または一度も文書化していないエンドポイントと、インタラクションのコールバックおよび元の応答のエンドポイントです。後者は[インタラクションのコンテキスト](responses.md)が扱います。

一部のグループは、サーバー内の Bot 以外の Discord の機能に対応しています。`monetization` と `application` は[収益化とアクティビティ](platform.md)に、`lobbies`、`identities`、`partner_sdk` は [Social SDK](platform.md#social-sdk) に、`application.update_role_connection_metadata` は[リンクされたロール](oauth2.md#linked-roles)に使います。
