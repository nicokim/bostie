---
source: a9ef813b389718cd5d31ae958953b39416d1a494
---

# ユーザーコマンド、メッセージコマンド、エントリーポイントコマンド

Discord には、スラッシュコマンドのほかに、ユーザーやメッセージのコンテキストメニューから実行するコマンドと、アプリのアクティビティを開くエントリーポイントコマンドがあります。

## ユーザーコマンドとメッセージコマンド { #user-and-message-commands }

```python
--8<-- "docs/snippets/commands/context_menus.py"
```

`@bot.user_command(name)` と `@bot.message_command(name)` は、コンテキストと対象を受け取る関数を登録します。名前はメニューに表示されるもので、1〜32 文字です。スラッシュコマンドの名前とは異なり、大文字と空白も使えます。これらのコマンドには説明もオプションもありません。

ハンドラーが何を受け取るかは、対象の型ヒントで決まります。

| コマンド | 型ヒント | ハンドラーが受け取るもの |
|---|---|---|
| ユーザー | `User` | そのユーザー |
| ユーザー | `ResolvedMember` | そのメンバー（`user` 付き）。サーバー内のみ |
| ユーザー | `User \| ResolvedMember` | サーバー内ではメンバー、それ以外ではユーザー |
| メッセージ | `Message` | ユーザーまたは Bot が送ったメッセージ |
| メッセージ | `WebhookMessage` | ウェブフックが送ったメッセージ（インタラクションへの応答を含む） |
| メッセージ | `Message \| WebhookMessage` | どちらか |

対象が型ヒントの型に解決されない場合は `OptionConversionError` が送出され、[エラーフック](checks.md#error-hooks)に渡されます。どちらか一方でしか意味をなさないコマンドでない限り、メッセージの対象の型ヒントは `Message | WebhookMessage` にしてください。

これらのコマンドは、`description`、`description_localizations`、オプションの設定を除くスラッシュコマンドの設定を受け付けます。つまり、`name_localizations`、`ephemeral`、`auto_defer`、`default_permissions`、`nsfw`、`guilds`、`contexts`、`integration_types`、`checks`、`extra` です。Discord が 1 つのスコープに許すのはユーザーコマンド 15 個とメッセージコマンド 15 個までで、それを超えると同期で警告が出ます。

## エントリーポイントコマンド { #the-entry-point-command }

アクティビティを持つアプリには、App Launcher に表示されるエントリーポイントコマンドが 1 つあります。`@bot.entry_point(name)` は、コンテキストだけを受け取る関数でこれを登録します。

```python
--8<-- "docs/snippets/commands/entry_point.py"
```

ハンドラーは他のコマンドと同じように応答します。`ctx.launch_activity()` はアクティビティを開いてそのインスタンスの ID を返し、`ctx.respond(...)` は代わりにメッセージで応答します。Developer Portal でアクティビティを有効にしていないアプリからのこのコマンドは、Discord に拒否されます。

エントリーポイントは常にグローバルです。`dev_guilds` が設定されている場合、同期はこれを除外して警告を出します。Discord に既定のエントリーポイントが別の名前ですでにある場合、同期はそのコマンドの ID を引き継ぐので、Discord は両方を残すのではなくそれを置き換えます。`launch_activity()` はコンポーネントとモーダルのコンテキストにもありますが、最初の応答でなければなりません。

`entry_point` は、`description`、`name_localizations`、`description_localizations`、`ephemeral`、`auto_defer`、`default_permissions`、`nsfw`、`contexts`、`integration_types`、`checks`、`extra` を受け付けます。アクティビティ側のその他の内容は、[アクティビティ、収益化、Social SDK](platform.md#activities) で扱います。
