---
source: 9f6f43faa726c1342aeebb13122e1615b76fb827
---

# メッセージの内容

メッセージは、どこに送る場合でも同じ要素から組み立てます。送り先は `ctx.respond`、`ctx.followup`、`ctx.edit` と `ctx.update`、`bot.rest.messages.create` と `edit`、そしてウェブフックの `execute` です。各要素はキーワード引数で、省略した要素は送信されません。

| 要素 | 型 | 補足 |
|---|---|---|
| `content` | `str` | `respond`、`followup`、`messages.create`、`execute` の最初の位置引数 |
| `embeds` | `Embed` オブジェクト | リッチな埋め込みを最大 10 個 |
| `components` | `ui` のビルダー | ボタン、セレクトメニュー、レイアウトコンポーネント。[コンポーネント](components.md)を参照 |
| `files` | `File` オブジェクト | アップロード。後述 |
| `allowed_mentions` | `AllowedMentions` | 通知を飛ばしてよいメンション。[許可するメンション](responses.md#allowed-mentions)を参照 |
| `flags` | `MessageFlags` | `SUPPRESS_EMBEDS` や `SUPPRESS_NOTIFICATIONS` など |
| `tts` | `bool` | そのチャンネルを開いている人に向けて内容を読み上げる |
| `poll` | `PollCreateRequest` | 投票。後述 |
| `ephemeral` | `bool` | インタラクションへの応答のみ |

`bot.rest.messages.create` は、ほかに `reply_to`、`message_reference`、`sticker_ids`、`nonce`、`enforce_nonce`、`shared_client_theme` を受け取り、[インタラクションへの応答](responses.md#messages-that-delete-themselves)と同じく `delete_after` も受け取ります。

## 埋め込みとファイル { #embeds-and-files }

```python
--8<-- "docs/snippets/responses/rich_messages.py"
```

`Embed` とその構成要素（`EmbedField`、`EmbedFooter`、`EmbedAuthor`、`EmbedMedia`）は、キーワード引数で作るイミュータブルなモデルです。Discord の JSON と同じ形の素の `dict` も使えます。`color` は `0x5865F2` のような `int` を受け取り、[`bostie.color`](formatting.md#colors) のヘルパーでその値を作れます。

`File(name, data)` は `data` を `name` という名前でアップロードします。`description` は代替テキストで、`spoiler=True` にするとぼかし表示になり、`title` は Discord が表示するタイトルを設定します。埋め込みやコンポーネントは、上のグラフの例のように `attachment://name` でアップロードを参照します。bostie はサイズを確認しません。Discord のアップロード上限は、サーバーのブーストとユーザーの Nitro によって変わります。

## 投票 { #polls }

`PollCreateRequest` は `question` と `answers` を受け取ります。どちらも `text` を持つ `PollMedia` で、回答では任意で `emoji` も指定できます。`duration` は時間単位で、省略すると 24 です。`allow_multiselect=True` にすると、ユーザーが複数の回答を選べるようになります。`bot.rest.polls.end(channel_id, message_id)` は投票を早めに締め切り、`bot.rest.polls.list_answer_voters(...)` はある回答を選んだユーザーを一覧にします。`PollVoteAdd` と `PollVoteRemove` の[イベント](events.md)は、投票をその都度知らせます。

## 編集と添付ファイル { #editing-and-attachments }

編集では、各要素をキーワードで渡し、さらに `attachments` も渡せます。省略した要素はそのまま残り、`None` を渡すと消えます。たとえば `ctx.edit(embeds=None)` は埋め込みを削除し、内容は残します。編集で `files` か `attachments` を渡すと、メッセージに残る添付ファイルは、`attachments` に `Attachment` オブジェクトまたは ID で挙げたものと、新しいファイルだけになります。

## Components V2 { #components-v2 }

`components` にレイアウトコンポーネント（`ui.text`、`ui.section`、`ui.container`、`ui.media_gallery`、`ui.file`、`ui.separator`、`ui.thumbnail`）を含むメッセージは、Components V2 のメッセージになります。bostie は `IS_COMPONENTS_V2` フラグを設定し、コンポーネントがメッセージ全体になります。このようなメッセージは `content`、`embeds`、スタンプ、投票を持てず、それらを渡すと bostie は `ValueError` を送出します。テキストは代わりに `ui.text` に入れてください。`ui.row` に入れただけのボタンとセレクトメニューは、内容を持つ従来のメッセージでも使えます。

## 返信 { #replies }

```python
--8<-- "docs/snippets/responses/replies.py"
```

`reply_to` には、同じチャンネルにあるメッセージの ID を渡します。メッセージを転送するには、代わりに `message_reference=MessageReference(type=MessageReferenceType.FORWARD, message_id=..., channel_id=...)` を渡します。この 2 つは併用できません。サーバーで `message.content` を読むには、[ゲートウェイのガイド](gateway.md#intents)で説明しているとおり、特権インテントである Message Content インテントが必要です。

## 入力中の表示 { #typing-indicator }

```python
--8<-- "docs/snippets/responses/typing_indicator.py"
```

`async with ctx.typing():` は、ブロックの実行中、インタラクションのチャンネルに Bot の入力中の表示を出します。Discord はこの表示を 10 秒間出すので、bostie はブロックの開始時に表示を送り、その後 9 秒ごとに送り直します。ブロックが終わると、例外で終わった場合も含めて送信を止めます。`await ctx.typing()` は一度だけ表示します。インタラクションの外では、`bot.rest.channels.trigger_typing_indicator(channel_id)` が同じことを行い、await しても `async with` で使ってもかまいません。Bot がそのチャンネルにメッセージを送ると、表示はすぐに消えます。

## ボイスメッセージ { #voice-messages }

```python
--8<-- "docs/snippets/responses/voice_memo.py"
```

`await voice.message(audio)` は、ファイルパスまたはバイト列を読み込み、長さを測り、Discord が描く波形を計算して、そのまま送れる `File` を返します。形式は名前の拡張子で決まります。Discord のクライアントは 48 kHz モノラルの Ogg Opus で録音します。ボイスメッセージはその 1 つのファイルだけで構成され、内容、埋め込み、コンポーネント、投票、スタンプは含められません。bostie は `IS_VOICE_MESSAGE` フラグを設定し、メッセージにほかのものが含まれていると `ValueError` を送出します。自分で作った `File` も、`duration_secs` と、Discord が描く 8 ビットのサンプルである `waveform` を持っていれば、ボイスメッセージになります。
