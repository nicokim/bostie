---
source: 542ddb2b37fd206f8480755434912ca6bbefa224
description: "テキスト入力、セレクト、ファイルのアップロード、ラジオグループ、チェックボックスを持つ Discord のモーダルを開き、値を読み取り、ハンドラー内で送信を待ちます。"
---

# モーダル

![フォームに記入して送信する Bostie](../assets/mascot/bostie-modals.webp){ align=right width="180" loading=lazy }

モーダルとは、ポップアップで表示されるフォームです。コマンドやコンポーネントがモーダルで応答し、ユーザーの送信は新しいインタラクションとして届いて、モーダルのルートがそれを処理します。

```python
--8<-- "docs/snippets/modals/application_form.py"
```

`ui.modal(custom_id, title, *components)` は、1〜45 文字のタイトルと、1〜5 個のコンポーネントを受け取ります。コンポーネントは `ui.label(...)` のフィールドと `ui.text(...)` の段落です。`await ctx.modal(...)` でモーダルを開きます。これはインタラクションへの最初の応答でなければならず、その後そのインタラクションにはほかの方法で応答できません。

`@bot.on_modal(template)` は、[`on_component`](components.md#routes) がクリックをルーティングするのと同じように送信をルーティングします。`{name}` と `{name:int}` のプレースホルダーを使う同じテンプレート、同じ `route.id(...)`、同じ `ephemeral`、`auto_defer`、`checks` の設定が使えます。

## フィールド { #fields }

`ui.label(label, component, description=None)` は、1 つの入力欄の上に、最大 45 文字のラベルと最大 100 文字の説明を置きます。

| 入力欄 | ビルダー | 読み取り方 |
|---|---|---|
| テキストボックス | `ui.text_input(custom_id, style=TextInputStyle.SHORT)` | `ctx.text(custom_id)`（`str`） |
| 選択肢のセレクトメニュー | `ui.string_select(custom_id, options)` | `ctx.values(custom_id)` |
| ユーザー、ロール、その両方、またはチャンネルのセレクトメニュー | `ui.user_select`、`ui.role_select`、`ui.mentionable_select`、`ui.channel_select` | `ctx.users`、`ctx.members`、`ctx.roles`、`ctx.channels`、または `ctx.ids` |
| ファイルのアップロード | `ui.file_upload(custom_id)` | `ctx.attachments(custom_id)`（アップロードされた `Attachment` オブジェクト） |
| 2〜10 個から 1 つを選ぶ | `ui.radio_group(custom_id, options)` | `ctx.choice(custom_id)`（値または `None`） |
| 1〜10 個から複数を選ぶ | `ui.checkbox_group(custom_id, options)` | `ctx.values(custom_id)` |
| はい・いいえのチェックボックス 1 つ | `ui.checkbox(custom_id, default=False)` | `ctx.checked(custom_id)`（`bool`） |

チェックボックス以外の入力欄はすべて `required` を受け取り、既定値は `True` です。`min_values=0` には `required=False` が必要です。`text_input` は、複数行にする `style=TextInputStyle.PARAGRAPH`、`placeholder`、あらかじめ値を入れておく `value`、`min_length`（0〜4000）、`max_length`（1〜4000）を受け取ります。`file_upload` は `min_values` と `max_values`（最大 10 ファイル）、`["image", ".pdf"]` のような `file_types` を受け取ります。ラジオボタンとチェックボックスの選択肢は `ui.group_option(label, value, description=None, default=False)` で作ります。`max_values` を指定しないチェックボックスグループでは、ユーザーはすべての選択肢にチェックを付けられます。

ビルダーは `ui.ModalDict`、`ui.LabelDict`、`ui.TextInputDict`、`ui.FileUploadDict`、`ui.RadioGroupDict`、`ui.CheckboxGroupDict`、`ui.CheckboxDict`、`ui.GroupOptionDict` を返します。`ModalContext` の読み取りメソッドは、入力欄のカスタム ID を受け取ります。モーダルにない ID を渡すと `KeyError` が送出され、チェックボックスに `ctx.text` を使うなど、種類の違う入力欄を読むと `TypeError` が送出されます。

## モーダルを開いたメッセージを更新する { #updating-the-message-that-opened-the-modal }

コンポーネントから開いたモーダルは、そのコンポーネントが置かれているメッセージを知っており、送信時にそのメッセージを編集できます。

```python
--8<-- "docs/snippets/modals/settings_modal.py"
```

`await ctx.update(...)` と `await ctx.defer_update()` は、クリックの場合と同じく送信に対しても使えます。コマンドから開いたモーダルには更新するメッセージがないので、これらは `InteractionResponded` を送出します。その場合は代わりに `respond` で応答してください。応答のない送信は `auto_defer` 秒後に遅延応答され、メッセージがある場合は `defer_update` が使われます。

## ハンドラー内で送信を待つ { #waiting-for-the-submit-inside-a-handler }

```python
--8<-- "docs/snippets/modals/inline_modal.py"
```

`await bot.wait_for_modal(*custom_ids, check=None)` は、カスタム ID が `custom_ids` のいずれかである次の送信（何も指定しなければ、任意のモーダルの次の送信）の `ModalContext` を返し、その送信に対してはルートを実行しません。2 人のユーザーが同時にコマンドを実行してもそれぞれ自分の送信を受け取れるよう、カスタム ID にはインタラクション ID のような一意な値を入れてください。ユーザーは送信せずにモーダルを閉じることができ、そのとき Discord は Bot に何も知らせないので、`asyncio.timeout()` で待機時間を区切ってください。
