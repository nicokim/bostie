---
source: d624adfef5ab8dd6ffcb780c8ed7ca6459e83b3f
description: "bostie.ui でボタン、セレクトメニュー、Components V2 のレイアウトを作り、Bot の再起動後も動き続けるハンドラーにカスタム ID をルーティングします。"
---

# コンポーネント

![大きなボタンを押す Bostie](../assets/mascot/bostie-components.webp){ align=right width="180" loading=lazy }

コンポーネントとは、メッセージに置くボタン、セレクトメニュー、レイアウト用のブロックのことです。`bostie.ui` はこれらを Discord の JSON と同じ形の素の辞書として組み立て、組み立てる時点で Discord の上限を確認します。コンポーネントが使われたときは、`@bot.on_component` で登録したハンドラーが応答します。

コンポーネントを組み立てるには `bostie.ui` を使います。`bostie.Button` や `bostie.ActionRow` などのコンポーネントのモデルは、デコードされたメッセージの `components` が保持するものです。`components=` はこれらのモデルも受け取るので、メッセージのコンポーネントを組み立て直さずに、`msgspec.structs.replace` で編集してそのまま送り返せます。ただし、モデルは Discord の上限を確認しません。

## レイアウト { #layout }

```python
--8<-- "docs/snippets/components/layout.py"
```

これらは Components V2 です。[メッセージの内容](messages.md#components-v2)で説明しているとおり、コンポーネントがメッセージ全体を構成し、`content` の代わりに `ui.text` を使います。

| ビルダー | 表示されるもの |
|---|---|
| `ui.text(markdown)` | Discord のマークダウンを使えるテキスト |
| `ui.section(*texts, accessory=...)` | 1〜3 個のテキストと、その横に置くボタンまたはサムネイル |
| `ui.thumbnail(url, description=None, spoiler=False)` | 小さな画像。セクションのアクセサリーとしてのみ使用 |
| `ui.media_gallery(*items)` | 1〜10 個の画像または動画。それぞれ `ui.gallery_item(url, description=None, spoiler=False)` で作る |
| `ui.file(filename, spoiler=False)` | メッセージと一緒にその名前でアップロードされたファイル |
| `ui.separator(divider=True, spacing=1)` | コンポーネント間の余白。`spacing=2` で大きくなり、`divider=False` でない限り線が入る |
| `ui.container(*components, accent_color=None, spoiler=False)` | 行、テキスト、セクション、ギャラリー、ファイル、区切りを囲み、色付きの縁を持つボックス |
| `ui.row(*components)` | 最大 5 個のボタン、または 1 個のセレクトメニュー |

URL に `attachment://name` を指定すると、メッセージと一緒にアップロードしたファイルを表示できます。どのビルダーも、任意の数値のコンポーネント ID を指定する `id=` を受け取ります。`ui.raw(dict)` は、`ui` にまだビルダーがないコンポーネントをそのまま渡します。項目が多すぎる場合、ラベルが長すぎる場合、ボタンと同じ行にセレクトメニューを入れた場合は、組み立てた時点で `ValueError` が送出されます。各ビルダーは `ui.ButtonDict`、`ui.RowDict`、`ui.TextDict`、`ui.SectionDict`、`ui.ThumbnailDict`、`ui.MediaGalleryDict`、`ui.GalleryItemDict`、`ui.FileDict`、`ui.SeparatorDict`、`ui.ContainerDict`、`ui.StringSelectDict`、`ui.SelectOptionDict`、`ui.EntitySelectDict`、`ui.SelectDefaultDict` といった型付きの辞書を返し、`ui.raw` は `ui.RawComponent` を返します。後述の `ballot` のように、自作のヘルパーにはこれらで型注釈を付けてください。

## ボタン { #buttons }

- `ui.button(label, custom_id=..., style=ButtonStyle.SECONDARY)` は、クリックされると `custom_id` を Bot に送ります。`style` は `PRIMARY`、`SECONDARY`、`SUCCESS`、`DANGER` のいずれかです。
- `ui.link_button(label, url)` は URL を開くだけで、Bot には届きません。
- `ui.premium_button(sku_id)` はアプリの SKU を販売用に提示するだけで、Bot には届きません。

ボタンは `emoji=` を受け取ります。指定できるのは、Unicode の絵文字、Discord が表示するとおりに書いたカスタム絵文字（`<:name:id>`、アニメーション絵文字なら `<a:name:id>`。`fmt.emoji` で作れる形式です）、または `Emoji` か `PartialEmoji` のモデルです。`PartialEmoji.parse(text)` はこの形式の文字列を読み取ります。ボタンは `disabled=True` も受け取ります。ラベルは最大 80 文字、カスタム ID は 1〜100 文字です。

## ルート { #routes }

ルートとは、テンプレートに当てはまるすべてのカスタム ID を受け持つハンドラーです。

```python
--8<-- "docs/snippets/components/routes.py"
```

`"vote:{side:int}:{topic}"` は `vote:0:Lunch at noon` にマッチします。`{name}` は改行を含まない 1 文字以上を、`{name:int}` は負の数を含む整数を取り出します。取り出した値はキーワード引数としてハンドラーに渡され、`int` の場合は変換されています。テンプレートでは、2 つのプレースホルダーの間にリテラルの文字列が必要です。プレースホルダーのないテンプレートは、そのカスタム ID に完全一致する場合だけマッチします。

`vote.id(side=0, topic=topic)` はルート自体からカスタム ID を組み立てるので、テンプレートは 1 回書くだけで済みます。ID が Discord の上限である 100 文字を超える場合や、解析し直して同じ値に戻らない場合は、`ValueError` を送出します。

状態は Python のオブジェクトではなくカスタム ID に保持されるので、そのルートが登録されている限り、先週送ったボタンも再起動後に動作します。`@bot.on_component` はコマンドと同じく `ephemeral`、`auto_defer`、`checks` を受け取ります。`await vote(ctx, side=0, topic="x")` のようにルートを呼び出すと、ハンドラーが呼び出されます。

どのルートにもマッチしないコンポーネントの使用には応答が返りません。Discord は「このインタラクションは失敗しました」と表示し、`InteractionCreate` をリッスンしていない限り、bostie は警告をログに記録します。

## コンポーネントへの応答 { #answering-a-component }

`ComponentContext` は、コマンドのコンテキストと同じ応答メソッドに加えて、次のメソッドを持ちます。

- `await ctx.update(**parts)` は、コンポーネントが置かれているメッセージを編集します。省略した要素はそのまま残り、`None` を渡すとその要素が消えます。
- `await ctx.defer_update()` は、目に見える応答を出さずに使用を受け付けます。その後 15 分以内であれば、`update` でメッセージを編集できます。
- `await ctx.modal(ui.modal(...))` は[モーダル](modals.md)を開きます。

`respond` は、コマンドの場合と同じく新しいメッセージを送ります。`auto_defer` 秒のあいだ応答のなかった使用は、`defer_update` で遅延応答されます。

## セレクトメニュー { #select-menus }

```python
--8<-- "docs/snippets/components/selects.py"
```

| ビルダー | ユーザーが選ぶもの | 選択の読み取り方 |
|---|---|---|
| `ui.string_select(custom_id, options)` | `ui.select_option(label, value)` で作った選択肢 | `ctx.values`（値） |
| `ui.user_select(custom_id)` | ユーザー | `ctx.users`、サーバー内では `ctx.members` も |
| `ui.role_select(custom_id)` | ロール | `ctx.roles` |
| `ui.mentionable_select(custom_id)` | ユーザーとロール | `ctx.users`、`ctx.members`、`ctx.roles` |
| `ui.channel_select(custom_id, channel_types=...)` | 指定した種類のチャンネルのみ | `ctx.channels` |

`ctx.ids` には、どのエンティティセレクトでも選ばれた ID が入ります。ユーザーセレクトで `ctx.values` を読むなど、種類の違うものを読むと `TypeError` が送出されます。

どのセレクトメニューも、`placeholder`（最大 150 文字）、`min_values` と `max_values`（既定値は 1、最大 25）、`disabled` を受け取ります。文字列セレクトの選択肢は 1〜25 個です。`select_option` は `description`、ボタンと同じ形式の `emoji`、選択済みとして表示する `default=True` を受け取ります。エンティティセレクトは選択済みとして表示する ID である `defaults` を受け取り、メンション可能セレクトは `default_users` と `default_roles` を受け取ります。`required` が意味を持つのは[モーダル](modals.md)の中だけです。

## ページ送り { #pages }

`ui.pager(route, page, count)` は、`{page:int}` プレースホルダーを持つルート向けに、前へ、無効化された `page / count` のラベル、次へ、の 3 つのボタンからなる行を返します。最初のページは 0 で、追加のキーワード引数はルートのほかのプレースホルダーを埋めます。ページ番号がカスタム ID に含まれるので、ページ送りは再起動後も動作します。[コンポーネントのサンプル](../examples.md#components)の `/tour` コマンドがこれを使っています。

## ハンドラー内でクリックを待つ { #waiting-for-a-click-inside-a-handler }

コマンドは、ルートを使わずに、コンポーネントの使用を 1 回だけ待つことができます。

```python
--8<-- "docs/snippets/components/confirm.py"
```

`await bot.wait_for_component(*custom_ids, check=None)` は、カスタム ID が `custom_ids` のいずれかである次の使用（何も指定しなければ、任意のコンポーネントの次の使用）のうち、`check` が真を返すものの `ComponentContext` を返します。返された使用に対しては、ルートは実行されません。待機は無期限に続くので、`asyncio.timeout()` で時間を区切ってください。`check` が送出した例外は、ここで送出されます。返すコンテキストのための `ephemeral` と `auto_defer` を受け取ります。
