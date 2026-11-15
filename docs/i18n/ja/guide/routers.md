---
source: 54b3a6f7e7f026cf8621c75603332297ea4c5a00
---

# ルーターとモジュール

`Router` は、Bot そのものではありませんが、Bot と同じようにコマンド、リスナー、ルート、タスク、チェック、フック、エラーフックを保持します。ルーターを使うと、Bot をモジュールに分割したり、コマンドのグループに共通の設定を与えたり、Bot の実行中に入れ替えたりできます。Bot 自体もルーターなので、このページの内容はすべて Bot でも使えます。

```python
--8<-- "docs/snippets/routers/moderation_router.py"
```

ルーターは Bot のすべてのデコレーターを持っています。`command`、`group`、`user_command`、`message_command`、`entry_point`、`listen`、`listen_raw`、`on_component`、`on_modal`、`every`、`check`、`before_invoke`、`after_invoke`、`on_error` です。`bot.include(router)` はそのすべてを Bot に追加し、後からルーターに登録されたものも追加します。ハンドラーの中では、`router.bot` は組み込み先の Bot です。組み込まれる前は `StateError` を送出します。

ルーターは入れ子にできます。`router.include(other)` も同じように動作し、外側のルーターを Bot に組み込めば両方が組み込まれます。

## 既定値 { #defaults }

`Router(...)` は、そこに登録されるコマンドとルートの既定値をキーワード引数で受け取ります。

| 既定値 | 適用対象 |
|---|---|
| `ephemeral` | そのコマンドとルートの応答 |
| `auto_defer` | そのコマンドとルート |
| `default_permissions` | そのトップレベルのコマンド |
| `nsfw` | そのトップレベルのコマンド |
| `contexts`、`integration_types` | そのグローバルコマンド |

コマンドやルートは、まず自身の設定を使い、次にそのルーターの既定値、次にそれを組み込んでいる各ルーターの既定値を近いものから順に、最後に Bot の既定値を使います。この例では、`/ban` は「メンバーをBAN」の権限を持たないメンバーには表示されず、サーバー内でのみ使え、エフェメラルで応答されます。そのため、実行したモデレーターだけがボタンを見て押すことができます。

ルーターのチェック、before フック、after フックは、そのハンドラーに適用されます。それを組み込んでいるルーターのものも、外側から順に実行されます。エラーフックは、[チェック、フック、エラー](checks.md#error-hooks)で説明しているとおり、近いルーターから外側に向かって探されます。

## モジュール { #modules }

`bot.include("greetings")` はモジュール `greetings` をインポートし、そこで `router` という名前が付いた `Router` を組み込みます。`router` を持たないモジュールは `TypeError` を送出します。

`bot.exclude("greetings")` は、ルーターが追加したものをすべて取り除き、そのタスクをキャンセルし、Python にそのモジュールとサブモジュールを忘れさせます。そのため、もう一度組み込むとファイルが新たにインポートされます。`bot.reload("greetings")` はその両方を 1 回の呼び出しで行います。新しいインポートや組み込みが失敗した場合は、古いルーターが組み込まれたまま残り、エラーが送出されます。リロードするとハンドラーはすぐに変わります。コマンドの定義が変わった場合は、[モジュール式のサンプル](../examples.md#modular-bot)の `/reload` コマンドのように、その後で `await bot.sync_commands()` を呼び出してください。

`exclude` と `reload` は `Router` オブジェクトも受け取ります。モジュールの `router` ではないルーターに使います。

## 競合 { #conflicts }

同じ名前の 2 つのコマンド、同じ名前の 2 つのユーザーコマンドまたはメッセージコマンド、同じテンプレートの 2 つのルート、あるいはタスクとして 2 回登録された 1 つの関数は、2 つ目が追加された時点で例外を送出します。ルーターが持ち込んだ場合も同じです。ルーターを組み込めるのは一度だけで、自分自身に組み込むことはできません。

## 中身を調べる { #looking-inside }

`router.commands`、`router.context_menus`、`router.component_routes`、`router.modal_routes`、`router.tasks`、`router.listeners` には、登録されたものが入っています。コマンドは名前とサーバーごと（グローバルなものは `("rules", ())` のように）、ユーザーコマンドとメッセージコマンドは種類、名前、サーバーごと、ルートはテンプレートごと、タスクは関数ごと、リスナーはイベントごとです。`router.walk_commands()` は、ハンドラーを持つすべてのスラッシュコマンドとサブコマンドを順に返します。`router.match_component(custom_id)` と `router.match_modal(custom_id)` は、カスタム ID が行き着くルートとそのプレースホルダーの値、または `None` を返します。`router.find_error_handlers(error)` は、例外を処理するエラーフックを近いものから順に返します。
