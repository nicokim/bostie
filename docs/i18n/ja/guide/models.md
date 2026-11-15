---
source: 8bfaf791038898b7b25fb584ff4496c345a14fcb
---

# モデルと列挙型

bostie が Discord からデコードするものはすべてモデルです。`Message`、`Guild`、`Member`、`Interaction` をはじめ約 200 種類あり、すべて `bostie` からインポートできます。モデルは凍結された `msgspec` の構造体で、JSON のバイト列から直接デコードされます。

```python
--8<-- "docs/snippets/models/inspect_message.py"
```

## 型は Discord のドキュメントに従う { #types-follow-discords-documentation }

各フィールドの型は、Discord のドキュメントの記述どおりです。

- Discord が必ず送るフィールドには既定値がなく、欠けることもありません。ドキュメントで `null` になりうるとされているものは `T | None` です。
- ドキュメントで省略可能とされているフィールドには既定値があります。リストなら `()`、フラグなら空のフラグセット、省略が false を意味する真偽値なら `False`、それ以外は `None` です。
- ID は `int`、タイムスタンプは UTC のタイムゾーン付き `datetime` オブジェクト、色は `int` です。
- Discord が部分オブジェクトを送る箇所では、フィールドの型は部分モデルになります。`PartialUser`、`PartialMember`、`PartialChannel`、`PartialMessage`、`PartialEmoji`、`PartialApplication`、`PartialIntegration`、`PartialEntitlement`、`UnavailableGuild` です。部分モデルは部分ペイロードにあるフィールドしか持たないため、存在しないフィールドへのアクセスは実行時の `None` ではなく型エラーになります。

1 つのペイロードが 2 つの形を取る次の 3 か所では、クラスが 2 つに分かれています。

- ウェブフックが送ったメッセージ（インタラクションへの応答を含む）は `WebhookMessage` で、その `author` は `PartialUser` です。それ以外のメッセージは、完全な `User` を author に持つ `Message` です。両方を受け付けるフィールドの型は `Message | WebhookMessage` です。
- オプション、セレクト、コマンドの対象から解決されたメンバーは `ResolvedMember` で、`user` を持ち、`deaf` と `mute` を持ちません。ゲートウェイや REST から得た `Member` は両方を持ちます。
- `message.mentions` 内のユーザーは `MentionedUser` です。これは、メッセージがサーバーで送信された場合に Discord が付け加える `member` を持つ `User` です。

インタラクションは種類に応じて `CommandInteraction`、`ComponentInteraction`、`ModalSubmitInteraction`、`AutocompleteInteraction` のいずれかになり、それぞれ型付きの `data` を持ちます。

## 変更への耐性 { #change-resilience }

Discord は予告なくフィールド、列挙値、コンポーネントの種類を追加します。そのときも Bot が壊れてはいけません。

- bostie が知らないフィールドは、デコード時に無視されます。
- 列挙型は未知の値を `UNKNOWN_<value>` という名前の安定したメンバーに対応付けます。このメンバーは値と等しいものとして比較されます（`MessageType(99).name == "UNKNOWN_99"`）。
- フラグは、bostie が知らないビットを `~` を含むすべての演算子を通して保持します。
- 未知のコンポーネントの種類は `UnknownComponent` にデコードされます。これは生の JSON を保持し、`components` に渡せば変更されずにそのまま送り返されます。未知のインタラクションの種類は `UnknownInteraction` にデコードされ、`InteractionCreate` リスナーには届きますが、コマンドやルートには届きません。
- `message.referenced_message` やコンポーネントインタラクションのメッセージのような入れ子のメッセージは、プロパティを読んだときに初めてデコードされます。そのため、壊れたメッセージが失敗するのは、それを読んだ箇所だけです。

必須フィールドが欠けているなど、クラスにまったく合わないイベントのペイロードは、一度だけログに記録され、`RawEvent` リスナーにのみ配信されます。詳しくは[イベント](events.md#raw-events-and-events-bostie-does-not-model)を参照してください。

## 生のペイロード { #raw-payloads }

ペイロード全体からデコードされたモデルは、そのペイロードを保持します。`model.raw_json` がバイト列を持ち、`model.raw` はそれを新しい `dict` にデコードします。メッセージの author のような入れ子のモデルでは `raw_json = None` です。すべてのイベントも `raw` と `raw_json` を持ちます。これらを使えば、bostie がモデル化する前に Discord が送ってくるどのフィールドにもアクセスできます。

モデルの `repr` には、`raw_json` と秘密情報（`token`、`access_token`、`refresh_token`、`id_token`）は含まれません。

## モデルの作成とコピー { #building-and-copying-models }

モデルはイミュータブルで、キーワード引数で作成します。`msgspec.structs.replace(model, field=value)` は、一部のフィールドを変更したコピーを返します。`Embed`、`AllowedMentions`、`PollCreateRequest`、`MessageReference`、`ForumTag`、`RecurrenceRule`、`AutoModerationAction` など、Discord に送るモデルは設定していないフィールドを省いて送信するので、デコードしたモデルは受け取ったときのまま送り返されます。

## モデルのヘルパー { #helpers-on-models }

| ヘルパー | 値 |
|---|---|
| `user.mention`、`member.mention`、`role.mention`、`channel.mention` | `<@123>` のようなメンションのテキスト |
| `user.display_name`、`member.display_name` | グローバル名またはユーザー名。メンバーの場合はニックネームが優先されます |
| `message.jump_url` | メッセージへのリンク |
| `message.author_member` | メッセージがメンバーを含む場合、`ResolvedMember` としての author |
| `role.permissions`、`overwrite.allow`、`overwrite.deny` | `Permissions` フラグ |
| `member.permissions` | Discord が送ってきた場合、インタラクションのチャンネルにおけるメンバーの権限 |
| `webhook.url` | ウェブフックがトークンを持つ場合、その URL |

メンション、タイムスタンプ、ID から作る CDN の URL については[書式のガイド](formatting.md)で扱います。

## Snowflake { #snowflakes }

Discord の ID には、オブジェクトが作成された日時が埋め込まれています。Snowflake の ID を持つすべてのモデルには `created_at` があり、その日時を UTC の `datetime` で表します。`message.created_at`、`member.created_at`（アカウントの作成日時）、`emoji.created_at`（Unicode 絵文字では `None`）などです。`bostie.snowflake_time(id)` は単独の ID に対して同じことを行います。`bostie.time_snowflake(when)` は、ある時点で作成されうる最小の ID を返し、`high=True` を指定すると最大の ID を返します。日付によるページングに使えます。タイムゾーンを持たない `datetime` はローカル時刻として扱われます。

## 列挙型 { #enums }

`bostie` は Discord の列挙型とフラグをエクスポートしています。`ChannelType`、`MessageType`、`Permissions`、`Intents`、`MessageFlags`、`Locale`、`ButtonStyle`、そのほか[列挙型のリファレンス](../reference/api/enums.md)に載っているものです。フラグは `|` で組み合わせ、`in` で判定します（`Permissions.SEND_MESSAGES in member_permissions`）。`message.type.deletable` は、その種類のメッセージを Discord が削除させるかどうかを示します。グループ DM と通話の通知、チャンネル名とアイコンの変更、スレッドの最初のメッセージは削除できません。`JSONErrorCode` は、`HTTPException.code` が報告する Discord のエラーコードに名前を付けたものです。
