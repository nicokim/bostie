---
source: 12cb33d1448fc6df340b3c11d616bf17c9f5b58c
description: "用 bostie.fmt 的提及、时间戳和 Markdown 转义格式化 Discord 文本，用 bostie.color 选颜色，并构建头像、图标和表情符号的 CDN URL。"
---

# 格式化、颜色和 CDN URL

三个小模块负责构建 Discord 会渲染的文本和 URL：`bostie.fmt` 用于 Markdown、提及和时间戳，`bostie.color` 用于颜色，`bostie.cdn` 用于图片 URL。

```python
--8<-- "docs/snippets/formatting/profile_card.py"
```

## 提及、时间戳和链接 { #mentions-timestamps-and-links }

| 函数 | 返回 | Discord 显示为 |
|---|---|---|
| `fmt.user(user_id)` | `<@id>` | 该用户，并通知对方 |
| `fmt.role(role_id)` | `<@&id>` | 该身份组，并通知其成员 |
| `fmt.channel(channel_id)` | `<#id>` | 指向该频道的链接 |
| `fmt.command(name, command_id)` | `</name:id>` | 一个可点击的命令；`name` 可以包含命令组和子命令 |
| `fmt.emoji(name, emoji_id, animated=False)` | `<:name:id>` | 一个自定义表情符号 |
| `fmt.game_profile(game_id)` | `<@$id>` | 一个游戏资料 |
| `fmt.timestamp(when, style=None)` | `<t:seconds:style>` | 按每位读者自己的时区和语言显示的时间 |
| `fmt.guild_navigation(kind, role_id=None)` | `<id:kind>` | 指向服务器某个标签页的链接：`"customize"`、`"browse"`、`"guide"` 或 `"linked-roles"`；只有 `"linked-roles"` 接受身份组 |
| `fmt.store(application_id, sku_id=None)` | 一个商店 URL | 应用的商店页面或某个 SKU，显示为一张卡片 |

`fmt.timestamp` 接受 `datetime` 或 Unix 秒数。它的样式与 Discord 一致：`"t"` 16:20，`"T"` 16:20:30，`"d"` 20/04/2021，`"D"` April 20, 2021，`"f"` April 20, 2021 at 16:20（未指定样式时 Discord 的默认样式），`"F"` 在此基础上加上星期，`"s"` 20/04/2021, 16:20，`"S"` 再加上秒，`"R"` 为相对时间，例如“in 5 minutes”。模型上的 `user.mention`、`role.mention` 和 `channel.mention` 与这些提及函数给出的文本相同，`bot.command_mention` 会替你查找命令的 ID（[注册命令](registration.md#command-mentions)）。

## 来自用户的文本 { #text-from-users }

- `fmt.escape_markdown(text)` 让 Markdown 按输入的原样显示，同时保持链接可用。
- `fmt.remove_markdown(text)` 去除 Markdown。
- `fmt.escape_mentions(text)` 通过插入零宽空格，破坏 `@everyone`、`@here` 以及用户和身份组的提及，使其无法发出通知。
- `fmt.mentioned_users(text)`、`fmt.mentioned_roles(text)` 和 `fmt.mentioned_channels(text)` 返回文本中提及的 ID。

转义只是一层防护；无论文本写了什么，最终由 [`allowed_mentions`](responses.md#allowed-mentions) 决定哪些提及能发出通知。

## 颜色 { #colors }

`bostie.color` 以 `int` 形式提供 Discord 的品牌色：`BLURPLE`、`GREEN`、`YELLOW`、`FUCHSIA` 和 `RED`。`color.rgb(red, green, blue)` 接受三个 0 到 255 的值，`color.hsv(hue, saturation, value)` 接受三个 0 到 1 的值，`color.parse("#5865F2")` 读取六位十六进制数字，可以带 `#`、`0x` 前缀，也可以都不带。嵌入内容的 `color`、容器的 `accent_color` 以及身份组都接受这些值。

## CDN URL { #cdn-urls }

`bostie.cdn` 用于构建 Discord 所存储图片的 URL。每个函数都接受模型中的 ID 和图片哈希，以及 `size`（16 到 4096 之间的 2 的幂）和 `format`（`"png"`、`"jpg"`、`"jpeg"`、`"webp"`（默认）或 `"gif"` 之一）。哈希以 `a_` 开头的动态图片在 WebP 或 GIF 格式下仍保持动态；对静态图片使用 `"gif"`，或使用不在列表中的尺寸，会引发 `ValueError`。`await bot.http.read(url)` 可以下载图片，[REST 指南](rest.md#downloading-files)中有说明。

| 函数 | 图片 |
|---|---|
| `display_avatar(user)` | 用户的头像，没有头像时为默认头像 |
| `user_avatar(user_id, avatar)`、`default_avatar(user_id)` | 用户自己的头像或默认头像 |
| `user_banner(user_id, banner)`、`avatar_decoration(asset)` | 用户的横幅和头像装饰 |
| `member_avatar(guild_id, user_id, avatar)`、`member_banner(...)` | 成员在服务器中专用的头像和横幅 |
| `guild_icon`、`guild_splash`、`guild_discovery_splash`、`guild_banner` | 服务器的各种图片，由 `guild_id` 和哈希构建 |
| `guild_tag_badge(guild_id, badge)` | 服务器标签的徽章 |
| `role_icon(role_id, icon)` | 身份组的图标 |
| `emoji(emoji_id, animated=False)` | 自定义表情符号 |
| `sticker(sticker_id, format_type)` | 贴纸，按其格式类型为 PNG、Lottie JSON 或 GIF |
| `sticker_pack_banner(banner_asset_id)` | 贴纸包的横幅 |
| `scheduled_event_cover(event_id, image)` | 服务器活动的封面 |
| `application_icon`、`application_cover`、`application_asset`、`store_page_asset` | 应用的图标、封面、Rich Presence 素材和商店图片 |
| `team_icon(team_id, icon)` | 团队的图标 |
| `soundboard_sound(sound_id)` | 音效板音效的音频 |
