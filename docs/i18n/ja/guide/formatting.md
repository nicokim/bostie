---
source: 12cb33d1448fc6df340b3c11d616bf17c9f5b58c
description: "Discord の書式。bostie.fmt のメンション、タイムスタンプ、エスケープ、bostie.color の色、アバター、アイコン、絵文字の CDN URL を解説します。"
---

# 書式、色、CDN URL

Discord が表示するテキストと URL は、3 つの小さなモジュールで組み立てます。マークダウン、メンション、タイムスタンプには `bostie.fmt`、色には `bostie.color`、画像の URL には `bostie.cdn` を使います。

```python
--8<-- "docs/snippets/formatting/profile_card.py"
```

## メンション、タイムスタンプ、リンク { #mentions-timestamps-and-links }

| 関数 | 戻り値 | Discord での表示 |
|---|---|---|
| `fmt.user(user_id)` | `<@id>` | ユーザー。そのユーザーに通知が届きます |
| `fmt.role(role_id)` | `<@&id>` | ロール。そのロールに通知が届きます |
| `fmt.channel(channel_id)` | `<#id>` | チャンネルへのリンク |
| `fmt.command(name, command_id)` | `</name:id>` | クリックできるコマンド。`name` にはグループとサブコマンドを含められます |
| `fmt.emoji(name, emoji_id, animated=False)` | `<:name:id>` | カスタム絵文字 |
| `fmt.game_profile(game_id)` | `<@$id>` | ゲームプロフィール |
| `fmt.timestamp(when, style=None)` | `<t:seconds:style>` | 読む人それぞれのタイムゾーンと言語で表示される時刻 |
| `fmt.guild_navigation(kind, role_id=None)` | `<id:kind>` | サーバーのタブへのリンク。`"customize"`、`"browse"`、`"guide"`、`"linked-roles"` のいずれかで、ロールを受け取るのは `"linked-roles"` だけです |
| `fmt.store(application_id, sku_id=None)` | ストアの URL | アプリのストアページ、または 1 つの SKU をカードとして表示します |

`fmt.timestamp` は `datetime` または Unix 秒を受け取ります。スタイルは Discord のもので、`"t"` は 16:20、`"T"` は 16:20:30、`"d"` は 20/04/2021、`"D"` は April 20, 2021、`"f"` は April 20, 2021 at 16:20（スタイルを指定しない場合の Discord の既定）、`"F"` はそれに曜日を加えたもの、`"s"` は 20/04/2021, 16:20、`"S"` はそれに秒を加えたもの、`"R"` は「5 分後」のような相対表示です。モデルの `user.mention`、`role.mention`、`channel.mention` はメンション関数と同じテキストを返し、`bot.command_mention` はコマンドの ID を代わりに調べてくれます（[コマンドの登録](registration.md#command-mentions)）。

## ユーザーからのテキスト { #text-from-users }

- `fmt.escape_markdown(text)` は、リンクを機能させたまま、マークダウンを入力どおりに表示させます。
- `fmt.remove_markdown(text)` はマークダウンを取り除きます。
- `fmt.escape_mentions(text)` は、ゼロ幅スペースを挿入して `@everyone`、`@here`、ユーザーとロールのメンションを崩し、通知が届かないようにします。
- `fmt.mentioned_users(text)`、`fmt.mentioned_roles(text)`、`fmt.mentioned_channels(text)` は、テキスト内でメンションされた ID を返します。

エスケープは防御の 1 層にすぎません。テキストの内容にかかわらず、何が通知を送れるかは [`allowed_mentions`](responses.md#allowed-mentions) が決めます。

## 色 { #colors }

`bostie.color` には、Discord のブランドカラーが `int` として `BLURPLE`、`GREEN`、`YELLOW`、`FUCHSIA`、`RED` の名前で用意されています。`color.rgb(red, green, blue)` は 0 から 255 までの 3 つの値を、`color.hsv(hue, saturation, value)` は 0 から 1 までの 3 つの値を受け取り、`color.parse("#5865F2")` は `#` や `0x` の有無にかかわらず 6 桁の 16 進数を読み取ります。埋め込みの `color`、コンテナの `accent_color`、ロールはこれらの値を受け付けます。

## CDN URL { #cdn-urls }

`bostie.cdn` は、Discord が保存している画像の URL を組み立てます。どの関数も、モデルから得た ID と画像のハッシュに加えて、16 から 4096 までの 2 のべき乗である `size` と、`"png"`、`"jpg"`、`"jpeg"`、`"webp"`（既定）、`"gif"` のいずれかである `format` を受け取ります。ハッシュが `a_` で始まるアニメーション画像は、WebP または GIF ではアニメーションのままになります。静止画に `"gif"` を指定したり、一覧にないサイズを指定したりすると `ValueError` が送出されます。[REST のガイド](rest.md#downloading-files)で説明しているとおり、`await bot.http.read(url)` で画像をダウンロードできます。

| 関数 | 画像 |
|---|---|
| `display_avatar(user)` | ユーザーのアバター。アバターがなければデフォルトのアバター |
| `user_avatar(user_id, avatar)`、`default_avatar(user_id)` | ユーザー自身のアバター、またはデフォルトのアバター |
| `user_banner(user_id, banner)`、`avatar_decoration(asset)` | ユーザーのバナーとアバターデコレーション |
| `member_avatar(guild_id, user_id, avatar)`、`member_banner(...)` | メンバーのサーバー別のアバターとバナー |
| `guild_icon`、`guild_splash`、`guild_discovery_splash`、`guild_banner` | `guild_id` とハッシュから得るサーバーの画像 |
| `guild_tag_badge(guild_id, badge)` | サーバータグのバッジ |
| `role_icon(role_id, icon)` | ロールのアイコン |
| `emoji(emoji_id, animated=False)` | カスタム絵文字 |
| `sticker(sticker_id, format_type)` | フォーマットの種類に応じた PNG、Lottie JSON、GIF のスタンプ |
| `sticker_pack_banner(banner_asset_id)` | スタンプパックのバナー |
| `scheduled_event_cover(event_id, image)` | イベントのカバー画像 |
| `application_icon`、`application_cover`、`application_asset`、`store_page_asset` | アプリのアイコン、カバー画像、リッチプレゼンスのアセット、ストアの画像 |
| `team_icon(team_id, icon)` | チームのアイコン |
| `soundboard_sound(sound_id)` | サウンドボードのサウンドの音声 |
