---
source: 620651abbb35486bdf47aba0669128b8bb1d0ad6
description: "Python の型ヒントで Discord のスラッシュコマンドを定義します。オプションの型、選択肢、範囲、オートコンプリート、コンバーター、サブコマンドグループ、上限を扱います。"
---

# スラッシュコマンド

![吹き出しの中にスラッシュコマンドを書いている Bostie](../assets/mascot/bostie-commands.webp){ align=right width="180" loading=lazy }

スラッシュコマンドは、`@bot.command` でデコレートした非同期関数です。最初のパラメーターは [`Context`](responses.md) を受け取り、それ以降のパラメーターはすべてコマンドのオプションになります。

```python
--8<-- "docs/snippets/commands/option_types.py"
```

このコマンドは `/report` です。名前は関数名、説明は docstring の 1 行目になります。`@bot.command(name=..., description=...)` を使うと代わりにこれらを指定でき、どちらもないコマンドの説明は `…` になります。`@bot.command` と `@bot.command(...)` はどちらも使え、Bot にも[ルーター](routers.md)にも付けられます。

## オプションの型 { #option-types }

パラメーターの型ヒントでオプションの型が決まり、ハンドラーは変換済みの値を受け取ります。

| 型ヒント | Discord のオプション | ハンドラーが受け取るもの |
|---|---|---|
| `str` | 文字列 | テキスト |
| `int` | 整数 | `int` |
| `float` | 数値 | `float`（ユーザーが整数を入力した場合も） |
| `bool` | 真偽値 | `bool` |
| `User` | ユーザー | その `User` |
| `ResolvedMember` | ユーザー | そのメンバー（`user` 付き）。サーバー内のみ |
| `Role` | ロール | その `Role` |
| `PartialChannel` | チャンネル | インタラクションが解決したとおりのチャンネル |
| `Attachment` | 添付ファイル | アップロードされたファイルの `Attachment` |
| `User \| Role` または `ResolvedMember \| Role` | メンション可能 | ユーザーが選んだほう |
| `Literal[...]` または `Enum` | 選択肢付きの文字列、整数、数値 | そのリテラル、または列挙型のメンバー |

表にない型ヒントを使うと、デコレーターの実行時に `CommandDefinitionError` が送出されます。チャンネルのオプションの型ヒントは `Channel` ではなく `PartialChannel`、メンバーは `Member` ではなく `ResolvedMember` です。インタラクションが運ぶのはこうした部分オブジェクトであり、エラーメッセージでもそのことが示されます。

## 必須と任意 { #required-and-optional }

既定値のないオプションは必須です。既定値を指定するか、型ヒントに `| None` を付けると任意になります。ユーザーが省略した任意のオプションには、その既定値または `None` が入ります。Discord は必須のオプションを先に並べることを求めるので、bostie はコマンドを組み立てる際に、パラメーターの順序にかかわらずその順に並べ替えます。

## 説明 { #descriptions }

`Annotated` 内の文字列がオプションの説明になります（`Annotated[str, "What happened"]`）。代わりに docstring に説明を書くこともできます。

```python
--8<-- "docs/snippets/commands/docstring_options.py"
```

bostie は、上の例のような `Args:` セクションと、ダッシュで下線を引いた `Parameters` セクションを読み取ります。説明は複数行にまたがってもかまいません。`Annotated` 内の文字列は `Option(description=)` より優先され、`Option(description=)` は docstring より優先されます。どこにも説明がないオプションの説明は `…` になります。

## 範囲とオプションの設定 { #ranges-and-option-settings }

`Range[int, 1, 10]` は 1 から 10 まで（両端を含む）の `int` オプションです。`Range` は `float` も受け付け、`str` の場合は範囲が長さを表します（`Range[str, 1, 2000]`）。それ以外の設定は `Annotated` 内の `Option(...)` で行い、型ヒントから導かれる設定よりも優先されます。

```python
--8<-- "docs/snippets/commands/option_settings.py"
```

| `Option` のフィールド | 対象 | 意味 |
|---|---|---|
| `name` | すべて | Discord 上でのオプション名。パラメーターは Python の名前のままです |
| `description` | すべて | 説明。`Annotated` に文字列がない場合に使われます |
| `name_localizations`、`description_localizations` | すべて | ロケールごとのテキスト（`{"fr": "salon"}` など） |
| `min`、`max` | 整数、数値 | 両端を含む範囲。±2⁵³ 以内 |
| `min_length`、`max_length` | 文字列 | 長さの範囲。それぞれ 0〜6000、1〜6000 |
| `channel_types` | チャンネル | ユーザーが選べるチャンネルの種類 |
| `file_types` | 添付ファイル | `image`、`video`、`audio`、または `.pdf` のような拡張子を最大 10 個 |
| `choices` | 文字列、整数、数値 | 固定の選択肢。下記を参照してください |
| `autocomplete` | 文字列、整数、数値 | ユーザーの入力に合わせて値を提案する関数 |
| `extra` | すべて | オプションの JSON にマージされる生のフィールド。bostie がまだモデル化していないフィールド用です |

対象外のオプションの型にフィールドを指定すると、`CommandDefinitionError` が送出されます。

## 選択肢 { #choices }

`Literal` や `Enum` は固定の選択肢のリストを提供します。`Literal` の選択肢は値がそのまま名前になります。`Enum` の選択肢はメンバー名が名前になり、送られるのはメンバーの値です。`Option(choices=[...])` を使うと、同じ値を同じ順序で並べている限り、選択肢の名前を変えたりローカライズしたりできます。

```python
--8<-- "docs/snippets/commands/option_choices.py"
```

`/order` のハンドラーは、`drink` には `"tea"`、`"coffee"`、`"water"` のいずれかを、`size` には `Size` のメンバーを、`milk` には選ばれた `Choice` の値を受け取ります。型ヒントが単なる `str`、`int`、`float` の場合は、`Option(choices=[...])` だけで選択肢を定義します。各選択肢は `Choice(name, value)` か、値そのもの（名前は値と同じになります）です。選択肢の値はオプションの型と一致している必要があり、`Literal` や `Enum` の値はすべて `str`、すべて `int`、またはすべて `float` でなければなりません。1 つのオプションが持てる選択肢は最大 25 個で、選択肢の名前は 1〜100 文字、文字列の値は最大 100 文字です。

## オートコンプリート { #autocomplete }

`Option(autocomplete=function)` を指定すると、ユーザーの入力中に `function` へ候補を問い合わせます。この関数は `AutocompleteContext` とそれまでに入力されたテキストを受け取り、値または `Choice` オブジェクトを返します。

```python
--8<-- "docs/snippets/commands/autocomplete_options.py"
```

`ctx.options` には、それまでに入力されたオプションがオプション名ごとに入っており、変換できるものはハンドラーが受け取るのと同じ形に変換されています。入力中のオプションはテキストのままです。bostie は最初の 25 件の候補を送り、100 文字を超える名前は切り詰め、名前が空のもの、値の型が違うもの、文字列の値が 100 文字を超えるものは取り除きます。いずれの場合も、それぞれ警告が出ます。1 つのオプションに `choices` と `autocomplete` の両方を指定することはできません。関数が例外を送出すると、そのエラーは[エラーフック](checks.md#error-hooks)に渡され、Discord には空のリストが送られます。

## コンバーター { #converters }

`Annotated[T, Convert(function)]` は、ハンドラーの実行前にオプションの値を `T` に変換します。`function(ctx, value)` は非同期でもかまいません。その `value` パラメーターの型ヒントで Discord のオプションの型が決まり、`Range` や `Annotated` の説明もそこから取られます。

```python
--8<-- "docs/snippets/commands/convert_options.py"
```

コンバーターが実行されるのは、ユーザーが指定したオプションに対してだけです。省略された任意のオプションには、変換されていない既定値が入ります。コンバーターからの例外は、ハンドラーからの例外と同じようにエラーフックに渡されます。

## サブコマンドとグループ { #subcommands-and-groups }

`bot.group(name, description)` はサブコマンドからなるコマンドを登録し、`group.group(...)` はその中にサブコマンドのグループを追加します。グループの入れ子は 1 段までで、1 つのグループには最大 25 個まで入れられます。

```python
--8<-- "docs/snippets/commands/command_groups.py"
```

この例は `/roles give` と `/roles color set` を定義します。`@group.command(name=...)` を使うと、サブコマンドに関数とは別の名前を付けられます。`ctx.command_name` は実行されたコマンドの完全な名前（`"roles color set"` など）で、コマンドオブジェクトでは `command.qualified_name` が同じものを表します。

トップレベルのグループは、`name`、`ephemeral`、`auto_defer` を除くコマンドの設定を受け付け、それらはすべてのサブコマンドに適用されます。`default_permissions`、`nsfw`、`guilds`、`contexts`、`integration_types` は、Discord ではトップレベルのコマンドにしか存在しないためです。入れ子のグループが受け付けるのは、2 つのローカライズ、`checks`、`extra` です。サブコマンドが受け付けるのは、`name`、`description`、2 つのローカライズ、`ephemeral`、`auto_defer`、`checks`、`extra` です。グループのチェックは、そのサブコマンドのチェックより先に実行されます。

## コマンドの設定 { #command-settings }

```python
--8<-- "docs/snippets/commands/command_settings.py"
```

| 設定 | 意味 |
|---|---|
| `name`、`description` | 関数のものの代わりに使う名前と説明 |
| `name_localizations`、`description_localizations` | ロケールごとのテキスト。翻訳関数については[コマンドの登録](registration.md#translations)を参照してください |
| `ephemeral` | 呼び出し側で別の指定をしない限り、応答はそのユーザーにだけ表示されます |
| `auto_defer` | 応答のない使用を遅延応答にするまでの秒数。`None` で無効になります（[インタラクションへの応答](responses.md#deferring)） |
| `default_permissions` | メンバーがコマンドを表示して使うのに必要な権限。誰が使えるかはサーバーの管理者が変更できます |
| `nsfw` | 年齢制限のあるチャンネルにだけ表示されます |
| `guilds` | コマンドをグローバルではなく登録するサーバーの ID |
| `contexts` | グローバルコマンドを使える場所（サーバー、Bot との DM、プライベートチャンネル） |
| `integration_types` | コマンドを持つインストール（サーバーインストール、ユーザーインストール） |
| `checks` | ハンドラーの前に実行されるチェック（[チェック](checks.md)） |
| `extra` | コマンドの JSON にマージされる生のフィールド |

`ephemeral`、`auto_defer`、`default_permissions`、`nsfw`、`contexts`、`integration_types` は[ルーター](routers.md#defaults)のすべてのコマンドに対してまとめて設定することもでき、`contexts`、`integration_types`、`auto_defer` は Bot のすべてのコマンドに対して設定できます。

## 上限 { #limits }

デコレーターは Discord が拒否する内容をチェックし、関数名とパラメーター名を示した `CommandDefinitionError` を送出します。

- 名前は英字、数字、`-`、`_` からなる 1〜32 文字で、大文字は使えません。ユーザーコマンドとメッセージコマンドの名前には、大文字と空白も使えます。
- 説明は 1〜100 文字です。
- 1 つのコマンドが持てるオプションは最大 25 個、1 つのオプションが持てる選択肢は最大 25 個です。
- `*args`、`**kwargs`、位置専用のオプション、型ヒントのないオプションは受け付けません。

コマンドの名前、説明、選択肢の値の合計が、Discord が許す 8000 文字を超えると、エラーではなく警告が出ます。同期では、1 つのスコープにスラッシュコマンドが 100 個、ユーザーコマンドが 15 個、メッセージコマンドが 15 個を超えると警告が出ます。

## 次のステップ { #next }

- [ユーザーコマンド、メッセージコマンド、エントリーポイントコマンド](context-menus.md)
- 1 つのサーバーまたはグローバルに Discord へ[コマンドを登録](registration.md)し、翻訳する
- [インタラクションへの応答](responses.md)
