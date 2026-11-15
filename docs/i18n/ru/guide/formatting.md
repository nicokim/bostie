---
source: f4afb2ae41854fb4e97a63590ca4fa1760172438
---

# Форматирование, цвета и URL в CDN

Три небольших модуля строят текст и URL, которые отображает Discord: `bostie.fmt` — разметку Markdown, упоминания и метки времени, `bostie.color` — цвета, а `bostie.cdn` — URL изображений.

```python
--8<-- "docs/snippets/formatting/profile_card.py"
```

## Упоминания, метки времени и ссылки { #mentions-timestamps-and-links }

| Функция | Возвращает | Что показывает Discord |
|---|---|---|
| `fmt.user(user_id)` | `<@id>` | пользователя, и пингует его |
| `fmt.role(role_id)` | `<@&id>` | роль, и пингует её |
| `fmt.channel(channel_id)` | `<#id>` | ссылку на канал |
| `fmt.command(name, command_id)` | `</name:id>` | кликабельную команду; `name` может содержать группу и подкоманду |
| `fmt.emoji(name, emoji_id, animated=False)` | `<:name:id>` | собственный эмодзи |
| `fmt.game_profile(game_id)` | `<@$id>` | игровой профиль |
| `fmt.timestamp(when, style=None)` | `<t:seconds:style>` | время в часовом поясе и на языке каждого читателя |
| `fmt.guild_navigation(kind, role_id=None)` | `<id:kind>` | ссылку на вкладку сервера: `"customize"`, `"browse"`, `"guide"` или `"linked-roles"`; роль принимает только `"linked-roles"` |
| `fmt.store(application_id, sku_id=None)` | URL магазина | страницу магазина приложения или один SKU в виде карточки |

`fmt.timestamp` принимает `datetime` или секунды Unix. Стили — те же, что у Discord: `"t"` 16:20, `"T"` 16:20:30, `"d"` 20/04/2021, `"D"` April 20, 2021, `"f"` April 20, 2021 at 16:20 (стиль Discord по умолчанию, если стиль не указан), `"F"` то же с днём недели, `"s"` 20/04/2021, 16:20, `"S"` с секундами и `"R"` — относительное время, например «через 5 минут». `user.mention`, `role.mention` и `channel.mention` у моделей дают тот же текст, что и функции упоминаний, а `bot.command_mention` сам находит ID команды ([«Регистрация команд»](registration.md#command-mentions)).

## Текст от пользователей { #text-from-users }

- `fmt.escape_markdown(text)` заставляет разметку Markdown отображаться так, как её набрали, оставляя ссылки рабочими.
- `fmt.remove_markdown(text)` удаляет её.
- `fmt.escape_mentions(text)` ломает `@everyone`, `@here` и упоминания пользователей и ролей, вставляя пробел нулевой ширины, чтобы они не могли пинговать.
- `fmt.mentioned_users(text)`, `fmt.mentioned_roles(text)` и `fmt.mentioned_channels(text)` возвращают ID, упомянутые в тексте.

Экранирование — лишь один уровень защиты; что может пинговать, решает [`allowed_mentions`](responses.md#allowed-mentions), что бы ни было написано в тексте.

## Цвета { #colors }

В `bostie.color` есть фирменные цвета Discord в виде `int`: `BLURPLE`, `GREEN`, `YELLOW`, `FUCHSIA` и `RED`. `color.rgb(red, green, blue)` принимает три значения от 0 до 255, `color.hsv(hue, saturation, value)` — три значения от 0 до 1, а `color.parse("#5865F2")` читает шесть шестнадцатеричных цифр с `#`, `0x` или без префикса. Эти значения принимают `color` эмбедов, `accent_color` контейнеров и роли.

## URL в CDN { #cdn-urls }

`bostie.cdn` строит URL изображений, которые хранит Discord. Каждая функция принимает ID и хеш изображения из модели, а также `size` — степень двойки от 16 до 4096 — и `format`: `"png"`, `"jpg"`, `"jpeg"`, `"webp"` (по умолчанию) или `"gif"`. Анимированное изображение, чей хеш начинается с `a_`, остаётся анимированным в WebP или GIF; `"gif"` для статичного изображения или размер не из списка вызывают `ValueError`. Скачать изображение можно через `await bot.http.read(url)`, как объясняет [руководство по REST](rest.md#downloading-files).

| Функция | Изображение |
|---|---|
| `display_avatar(user)` | аватар пользователя или аватар по умолчанию, если своего нет |
| `user_avatar(user_id, avatar)`, `default_avatar(user_id)` | собственный аватар пользователя или аватар по умолчанию |
| `user_banner(user_id, banner)`, `avatar_decoration(asset)` | баннер пользователя и украшение аватара |
| `member_avatar(guild_id, user_id, avatar)`, `member_banner(...)` | аватар и баннер участника для конкретного сервера |
| `guild_icon`, `guild_splash`, `guild_discovery_splash`, `guild_banner` | изображения сервера по `guild_id` и хешу |
| `guild_tag_badge(guild_id, badge)` | значок тега сервера |
| `role_icon(role_id, icon)` | значок роли |
| `emoji(emoji_id, animated=False)` | собственный эмодзи |
| `sticker(sticker_id, format_type)` | стикер в PNG, Lottie JSON или GIF в зависимости от типа формата |
| `sticker_pack_banner(banner_asset_id)` | баннер набора стикеров |
| `scheduled_event_cover(event_id, image)` | обложка запланированного события |
| `application_icon`, `application_cover`, `application_asset`, `store_page_asset` | значок, обложка, ресурсы Rich Presence и изображения магазина приложения |
| `team_icon(team_id, icon)` | значок команды |
| `soundboard_sound(sound_id)` | звук звуковой панели |
