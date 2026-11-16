---
source: b1ea93c8ecd81e5df88e6e3728f48038a3213951
description: "Discord のインタラクションへの応答、遅延応答、編集、フォローアップ、エフェメラルなメッセージ、自動遅延応答、3 秒と 15 分の制限、許可するメンションを解説します。"
---

# インタラクションへの応答

スラッシュコマンド、コンテキストメニューのコマンド、ボタン、セレクトメニュー、モーダルの送信は、どれもインタラクションです。Discord はアプリがそれに応答するのを待ちます。コマンドのハンドラーは `Context` を、コンポーネントのハンドラーは `ComponentContext` を、モーダルのハンドラーは `ModalContext` を受け取り、いずれも以下の応答用メソッドを共通で持ちます。

## コンテキストが持つ情報 { #what-a-context-knows }

| 属性 | 値 |
|---|---|
| `ctx.user` | 操作した `User`（サーバー内でも DM でも） |
| `ctx.member` | サーバー内ではその人の `Member`、それ以外では `None` |
| `ctx.guild_id`、`ctx.channel_id` | 操作が行われた場所。DM では `guild_id` は `None` です |
| `ctx.channel` | インタラクションに含まれる `PartialChannel` |
| `ctx.locale` | ユーザーのクライアントの言語 |
| `ctx.interaction` | インタラクションのモデル全体。`app_permissions` や `guild_locale` など、その他の情報に使います |
| `ctx.bot` | Bot |
| `ctx.voice` | このサーバーとユーザーの[ボイス](../voice/index.md) |
| `ctx.command_name` | `Context` では、サブコマンドを含むコマンド名（`"roles color set"` など） |

## 応答、編集、フォローアップ { #answer-edit-follow-up }

```python
--8<-- "docs/snippets/responses/answer_flow.py"
```

`ctx.respond(...)` は最初の応答を送ります。もう一度呼び出すと代わりにフォローアップメッセージを送るので、ハンドラーは必要なだけ何度でも呼び出せます。戻り値は Discord が作成したメッセージで、`WebhookMessage` です。

- `ctx.edit(**parts)` は最初の応答を、`ctx.edit(message_id, **parts)` はフォローアップを編集します。
- `ctx.followup(...)` は常に新しいメッセージを送ります。ただし `defer()` の直後は例外で、遅延した応答を埋めます。
- `ctx.fetch()` と `ctx.fetch(message_id)` は、応答またはフォローアップを Discord 上の現在の状態で返します。
- `ctx.delete()` と `ctx.delete(message_id)` はそれらを削除します。

`respond` と `followup` は、`content` と[メッセージの内容](messages.md)に挙げた各要素（`embeds`、`components`、`files`、`allowed_mentions`、`flags`、`tts`、`poll`）に加えて、`ephemeral` を受け付けます。編集も同じ要素をキーワード引数で受け付けます。省略した要素はそのまま残り、`None` を渡すとその要素は消去されます。

## 自動で削除されるメッセージ { #messages-that-delete-themselves }

```python
--8<-- "docs/snippets/responses/short_lived.py"
```

`respond`、`followup`、`bot.rest.messages.create` に `delete_after=` を渡すと、送ったメッセージがその秒数後に削除されます。削除は Bot がバックグラウンドで行い、Bot の終了時に取り消されるので、Bot の停止後に削除予定だったメッセージは残ります。インタラクションのメッセージはそのトークンを使って削除されます。この方法はエフェメラルなメッセージにも使えますが、インタラクションから 15 分で使えなくなるため、それより後に予定された削除は失敗し、警告がログに出力されます。すでに誰かが削除したメッセージは、何も出力せずにスキップされます。

## エフェメラルな応答 { #ephemeral-answers }

呼び出しに `ephemeral=True` を渡すと、そのメッセージはそのユーザーにだけ表示されます。コマンド、コンポーネントのルート、モーダルのルート、ルーターに `ephemeral=True` を設定すると、それらの応答の既定値になり、個々の呼び出しで `ephemeral=False` を渡すこともできます。既存のメッセージをエフェメラルと公開の間で切り替えることはできません。そうした編集を求めると、警告が 1 回ログに出力され、メッセージはそのまま残ります。

## 遅延応答 { #deferring }

最初の応答は 3 秒以内に Discord に届く必要があります。`await ctx.defer()` を呼ぶと、応答の代わりに「考え中…」が表示され、ハンドラーはその後 15 分以内に `respond` すればよくなります。`defer(ephemeral=True)` を使うと、後から送る応答がエフェメラルになります。

```python
--8<-- "docs/snippets/responses/deferring.py"
```

bostie は自動でも遅延応答を行います。届いてから `auto_defer` 秒経っても応答のない使用は遅延応答になり、既定値は 2.5 秒です。メッセージから開かれたコンポーネントやモーダルは `defer_update` で遅延応答になり、`update` で編集されるまでメッセージはそのまま残ります。`auto_defer` は Bot、ルーター、コマンド、ルートに設定でき、`None` を指定するとそこで無効になります。タイマーはゲートウェイがインタラクションを届けた時点から数えるので、イベントループがブロックされるとハンドラーの開始は遅れますが、期限は延びません。ハンドラーの開始が 1 秒以上遅れると、bostie は警告をログに出力します。

## モーダルとアクティビティ { #modals-and-activities }

`await ctx.modal(ui.modal(...))` はポップアップのフォームで応答します。詳しくは[モーダル](modals.md)を参照してください。`await ctx.launch_activity()` はアプリのアクティビティを開き、そのインスタンスの ID を返します。どちらも最初の応答でなければならず、モーダルの後にはどの呼び出しもインタラクションに応答できません。モーダルの送信は新しいインタラクションになります。

## 規則と、違反したときのエラー { #the-rules-and-the-errors-for-breaking-them }

各呼び出しは、まずインタラクションの状態を確認します。

| 呼び出し | 応答前 | `defer()` の後 | `defer_update()` の後 | 応答の後 |
|---|---|---|---|---|
| `respond` | 応答する | 応答を埋める | フォローアップを送る | フォローアップを送る |
| `defer` | 遅延応答する | 何もしない | 何もしない | `InteractionResponded` |
| `edit`、`delete` | `InteractionNotAnswered` | 応答を編集・削除する | メッセージを編集・削除する | 応答を編集・削除する |
| `followup` | `InteractionNotAnswered` | 応答を埋める | フォローアップを送る | フォローアップを送る |
| `update` | メッセージを編集する | `InteractionResponded` | メッセージを編集する | `InteractionResponded` |
| `modal`、`launch_activity` | 応答する | `InteractionResponded` | `InteractionResponded` | `InteractionResponded` |

`update` と `defer_update` があるのは、コンポーネントとモーダルのコンテキストだけです。モーダルの後に行った呼び出しは、どれも `InteractionResponded` を送出します。

インタラクションのトークンの有効期間は 15 分です。それを過ぎた後、または Discord がトークンを無効と返した後の呼び出しは、何も送らずに `InteractionExpired` を送出します。

応答せずに戻ったハンドラーでは、ユーザーには「アプリケーションが応答しませんでした」と表示され、bostie はそのハンドラーの名前を示した警告をログに出力します。遅延応答をしたまま応答しなかったハンドラーでは、トークンの期限が切れるまで「考え中…」が表示されたままになり、この場合も警告が出ます。ハンドラーが応答前に例外を送出すると、[エラーフック](checks.md#error-hooks)が応答しない限り、bostie が「Something went wrong.」と応答します。この応答は、ハンドラーが遅延応答をしていなければエフェメラルになります。

ユーザーにだけインストールされたアプリのインタラクションでは、Discord が許すフォローアップは 5 件までで、bostie は 6 件目で警告を出します。

## 許可するメンション { #allowed-mentions }

既定では、応答はユーザーとロールに通知を送れますが、`@everyone` と `@here` には送れません。`Bot(allowed_mentions=...)` の既定値は `AllowedMentions(parse=("users", "roles"))` です。メッセージと一緒に渡した `allowed_mentions` は、自身が設定した種類のメンションだけを変更します。そのため `AllowedMentions(users=(123,))` はユーザーへの通知を制限し、ロールの設定には影響しません。`AllowedMentions(parse=())` はすべてのメンションの通知を止め、`replied_user=False` を指定すると、返信先のメッセージの送信者に通知が届かなくなります。
