# Formatting, colors and CDN URLs

Three small modules build the text and URLs that Discord renders: `bostie.fmt` for markdown, mentions and timestamps, `bostie.color` for colors, and `bostie.cdn` for image URLs.

```python
--8<-- "docs/snippets/formatting/profile_card.py"
```

## Mentions, timestamps and links

| Function | Returns | Discord shows |
|---|---|---|
| `fmt.user(user_id)` | `<@id>` | the user, and pings them |
| `fmt.role(role_id)` | `<@&id>` | the role, and pings it |
| `fmt.channel(channel_id)` | `<#id>` | a link to the channel |
| `fmt.command(name, command_id)` | `</name:id>` | a clickable command; `name` may hold a group and subcommand |
| `fmt.emoji(name, emoji_id, animated=False)` | `<:name:id>` | a custom emoji |
| `fmt.game_profile(game_id)` | `<@$id>` | a game profile |
| `fmt.timestamp(when, style=None)` | `<t:seconds:style>` | a time in each reader's own time zone and language |
| `fmt.guild_navigation(kind, role_id=None)` | `<id:kind>` | a link to a guild tab: `"customize"`, `"browse"`, `"guide"` or `"linked-roles"`; only `"linked-roles"` takes a role |
| `fmt.store(application_id, sku_id=None)` | a store URL | the app's store page, or one SKU, as a card |

`fmt.timestamp` takes a `datetime` or Unix seconds. Its styles are Discord's: `"t"` 16:20, `"T"` 16:20:30, `"d"` 20/04/2021, `"D"` April 20, 2021, `"f"` April 20, 2021 at 16:20 (Discord's default when no style is given), `"F"` the same with the weekday, `"s"` 20/04/2021, 16:20, `"S"` with seconds, and `"R"` relative, such as "in 5 minutes". `user.mention`, `role.mention` and `channel.mention` on models give the same text as the mention functions, and `bot.command_mention` looks up a command's id for you ([Registering commands](registration.md#command-mentions)).

## Text from users

- `fmt.escape_markdown(text)` makes markdown show as typed, leaving links working.
- `fmt.remove_markdown(text)` strips it.
- `fmt.escape_mentions(text)` breaks `@everyone`, `@here` and user and role mentions so they cannot ping, by inserting a zero-width space.
- `fmt.mentioned_users(text)`, `fmt.mentioned_roles(text)` and `fmt.mentioned_channels(text)` return the ids mentioned in a text.

Escaping is one layer; [`allowed_mentions`](responses.md#allowed-mentions) decides what can ping, whatever the text says.

## Colors

`bostie.color` has Discord's brand colors as `int`: `BLURPLE`, `GREEN`, `YELLOW`, `FUCHSIA` and `RED`. `color.rgb(red, green, blue)` takes three values from 0 to 255, `color.hsv(hue, saturation, value)` three from 0 to 1, and `color.parse("#5865F2")` reads six hex digits, with `#`, `0x` or neither. Embeds' `color`, containers' `accent_color` and roles take these values.

## CDN URLs

`bostie.cdn` builds the URLs of images Discord stores. Each takes the ids and the image hash from a model, plus `size`, a power of two from 16 to 4096, and `format`, one of `"png"`, `"jpg"`, `"jpeg"`, `"webp"` (the default) or `"gif"`. An animated image, whose hash starts with `a_`, stays animated in WebP or GIF; `"gif"` for a still image, or a size off the list, raises `ValueError`.

| Function | Image |
|---|---|
| `display_avatar(user)` | the user's avatar, or the default avatar when they have none |
| `user_avatar(user_id, avatar)`, `default_avatar(user_id)` | a user's own or default avatar |
| `user_banner(user_id, banner)`, `avatar_decoration(asset)` | a user's banner and avatar decoration |
| `member_avatar(guild_id, user_id, avatar)`, `member_banner(...)` | a member's guild-specific avatar and banner |
| `guild_icon`, `guild_splash`, `guild_discovery_splash`, `guild_banner` | a guild's images, from `guild_id` and the hash |
| `guild_tag_badge(guild_id, badge)` | the badge of a guild tag |
| `role_icon(role_id, icon)` | a role's icon |
| `emoji(emoji_id, animated=False)` | a custom emoji |
| `sticker(sticker_id, format_type)` | a sticker as PNG, Lottie JSON or GIF, by its format type |
| `sticker_pack_banner(banner_asset_id)` | a sticker pack's banner |
| `scheduled_event_cover(event_id, image)` | a scheduled event's cover |
| `application_icon`, `application_cover`, `application_asset`, `store_page_asset` | an app's icon, cover, Rich Presence assets and store images |
| `team_icon(team_id, icon)` | a team's icon |
| `soundboard_sound(sound_id)` | a soundboard sound's audio |
