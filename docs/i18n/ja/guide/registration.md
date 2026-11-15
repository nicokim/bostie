---
source: 9aa2f3cd3cfe588188668066e047191f9b207226
---

# コマンドの登録

Discord がコマンドを表示するのは、アプリがそのコマンドを登録した後だけです。bostie はこれを代わりに行います。Bot の起動時に、定義したコマンドと Discord が保持しているコマンドを比較し、違いがある場合にだけ送信します。

## 起動時の同期 { #the-sync-at-startup }

`bot.start()` と `bot.run()` は、ゲートウェイに接続する前に同期を行います。bostie はスコープ（グローバル、または 1 つのサーバー）ごとに、登録済みのコマンドをローカライズ付きで取得してフィールドごとに比較し、違いがあればそのスコープのコマンドを 1 回のリクエストで置き換えます。ログには `commands up to date (global)` または `commands synced (global): added [...], removed [...], changed [...]` と出力されます。

Discord が保持していても、もう定義していないコマンドは削除されます。ただし、一度も定義しない種類のコマンドは例外です。`@bot.entry_point` のない Bot では、Discord がアクティビティのために作ったエントリーポイントコマンドが残ります。

`Bot(auto_sync=False)` を指定すると同期を行いません。`await bot.sync_commands()` は、変更の有無にかかわらずすべてのスコープのコマンドをその場で置き換え、スコープごとに `SyncResult` を返します。これには `added`、`removed`、`changed` になった名前と、Discord がスラッシュコマンドに割り当てた `ids` が入っています。

## スコープ { #scopes }

`guilds` のないコマンドはグローバルです。アプリをインストールしたすべてのサーバーで動作し、`contexts` が許していれば DM でも動作します。`guilds=[...]` を指定すると、そのサーバーにだけ登録されます。

Bot の開発中は、すべてをテスト用のサーバーに登録してください。

```python
--8<-- "docs/snippets/commands/command_sync.py"
```

`Bot(dev_guilds=[...])` は、自身の `guilds` を持たないすべてのコマンドを、グローバルではなくそのサーバーに登録します。エントリーポイントコマンドは常にグローバルなので、同期はこれを除外して警告を出します。

### 複数のスコープで同じ名前を使う { #one-name-in-several-scopes }

```python
--8<-- "docs/snippets/commands/guild_variants.py"
```

同じサーバーに 2 つ存在しない限り、コマンドは名前を共有できます。たとえば、グローバルの `/rules` と 1 つのサーバー用の `/rules`、あるいはサーバーごとの `/rules` です。それぞれが自分のスコープに登録され、インタラクションはそのサーバーに登録されたコマンドを実行し、なければグローバルのコマンドを実行します。そのサーバーでは、Discord はグローバルのコマンドとサーバーのコマンドの両方を一覧に表示します。`dev_guilds` の下では、サーバー独自のコマンドがそのサーバーでのグローバルのコマンドの代わりになります。同じ名前のコマンドが 2 つあり、そのサーバーが重なっている場合や、どちらもグローバルの場合は、2 つ目を登録した時点で `CommandDefinitionError` が送出されます。ユーザーコマンドとメッセージコマンドも同じ規則に従います。

同期が扱うのは、コマンドがあるスコープだけです。`dev_guilds` をやめても、コマンドはテスト用のサーバーに新しいグローバルのコマンドと並んで残ります。`await bot.rest.commands.bulk_overwrite_guild(bot.application_id, guild_id, [])` で削除できます。

## コマンドを使える場所 { #where-commands-can-be-used }

`contexts` と `integration_types` はグローバルコマンドに適用されます。コマンド、ルーター、Bot のいずれかに設定します。

- `contexts`：`InteractionContextType.GUILD`、`BOT_DM`（Bot との DM）、`PRIVATE_CHANNEL`（ユーザーインストールされたアプリでの、その他の DM とグループ DM）。
- `integration_types`：`ApplicationIntegrationType.GUILD_INSTALL` と `USER_INSTALL`。

`Bot(contexts=[...], integration_types=[...])` は、自分で設定していないすべてのグローバルコマンドの既定値になります。どこにも設定しなければ、bostie はどちらのフィールドも送らず、Discord の既定値が適用されます。`integration_types` の場合、それはアプリに設定されたインストールコンテキストです。設定していた値を外すことも変更とみなされるので、次の同期でコマンドは再び Discord の既定値に戻ります。

## コマンドのメンション { #command-mentions }

`await bot.command_mention("roles color set")` は `</roles color set:ID>` を返し、Discord はこれをクリックできるコマンドとして表示します。直前の同期で得た ID を使うか、スコープごとに 1 回 Discord に問い合わせます。`guild_id=` を指定すると、そのサーバーに登録されたコマンドが同じ名前のグローバルのコマンドより優先されます。未知の名前を渡すと `LookupError` が送出され、Bot の起動前に呼び出すと `StateError` が送出されます。すでに ID を持っている場合は、`fmt.command(name, command_id)` で同じテキストを組み立てられます。

## 翻訳 { #translations }

コマンド、グループ、サブコマンド、`Option`、`Choice` の `name_localizations` と `description_localizations` は、1 つのテキストを翻訳します。翻訳関数を使うと、すべてをまとめて翻訳できます。

```python
--8<-- "docs/snippets/commands/command_translations.py"
```

`Bot(translate=function)` は同期の際に、すべてのコマンド、グループ、サブコマンド、オプションのすべての名前と説明、そしてすべての選択肢の名前について、`Locale` ごとに 1 回ずつ `function(text, locale)` を呼び出します。`None` を返すか、テキストを変更せずに返すと、そのロケールには何も追加されません。自分で書いたローカライズは関数の結果より優先されます。ハンドラーでは、ユーザーの言語は `ctx.locale`、サーバーの言語は `ctx.interaction.guild_locale` で確認できます。

## Discord がコマンドを拒否したとき { #when-discord-refuses-the-commands }

Discord が 400 で拒否した同期では、`CommandSyncError` が送出されます。そのメッセージには問題ごとに 1 行ずつ、コマンド、オプションのパス、Discord のメッセージが示され、`error.problems` にはそれらが `SyncProblem` オブジェクトとして入っています。ほとんどの問題はもっと早い段階で見つかります。デコレーターが実行時に名前、説明、上限をチェックするためです。
