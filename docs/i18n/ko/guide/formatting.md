---
source: 12cb33d1448fc6df340b3c11d616bf17c9f5b58c
description: "bostie.fmt로 멘션, 타임스탬프, 마크다운 이스케이프를 처리하고, bostie.color로 색상을 고르며, 아바타와 이모지 CDN URL을 만듭니다."
---

# 서식, 색상, CDN URL

작은 모듈 세 개가 Discord가 렌더링하는 텍스트와 URL을 만듭니다. `bostie.fmt`는 마크다운, 멘션, 타임스탬프를, `bostie.color`는 색상을, `bostie.cdn`은 이미지 URL을 담당합니다.

```python
--8<-- "docs/snippets/formatting/profile_card.py"
```

## 멘션, 타임스탬프, 링크 { #mentions-timestamps-and-links }

| 함수 | 반환값 | Discord 표시 |
|---|---|---|
| `fmt.user(user_id)` | `<@id>` | 사용자를 표시하고 알림을 보냅니다 |
| `fmt.role(role_id)` | `<@&id>` | 역할을 표시하고 알림을 보냅니다 |
| `fmt.channel(channel_id)` | `<#id>` | 채널 링크 |
| `fmt.command(name, command_id)` | `</name:id>` | 클릭할 수 있는 명령어. `name`에는 그룹과 하위 명령어를 넣을 수 있습니다 |
| `fmt.emoji(name, emoji_id, animated=False)` | `<:name:id>` | 사용자 지정 이모지 |
| `fmt.game_profile(game_id)` | `<@$id>` | 게임 프로필 |
| `fmt.timestamp(when, style=None)` | `<t:seconds:style>` | 읽는 사람마다 자신의 시간대와 언어로 보이는 시각 |
| `fmt.guild_navigation(kind, role_id=None)` | `<id:kind>` | 서버 탭 링크: `"customize"`, `"browse"`, `"guide"`, `"linked-roles"`. 역할을 받는 것은 `"linked-roles"`뿐입니다 |
| `fmt.store(application_id, sku_id=None)` | 상점 URL | 앱의 상점 페이지나 SKU 하나를 카드로 표시합니다 |

`fmt.timestamp`는 `datetime`이나 Unix 초를 받습니다. 스타일은 Discord의 것을 그대로 따릅니다. `"t"` 16:20, `"T"` 16:20:30, `"d"` 20/04/2021, `"D"` April 20, 2021, `"f"` April 20, 2021 at 16:20(스타일을 지정하지 않을 때 Discord의 기본값), `"F"` 여기에 요일을 더한 형식, `"s"` 20/04/2021, 16:20, `"S"` 여기에 초를 더한 형식, `"R"` "in 5 minutes"처럼 상대적인 시간입니다. 모델의 `user.mention`, `role.mention`, `channel.mention`은 멘션 함수와 같은 텍스트를 돌려주며, `bot.command_mention`은 명령어의 ID를 대신 찾아 줍니다([명령어 등록](registration.md#command-mentions)).

## 사용자가 입력한 텍스트 { #text-from-users }

- `fmt.escape_markdown(text)`는 링크는 그대로 동작하게 두고, 마크다운이 입력한 그대로 보이게 합니다.
- `fmt.remove_markdown(text)`는 마크다운을 제거합니다.
- `fmt.escape_mentions(text)`는 폭이 없는 공백(zero-width space)을 넣어 `@everyone`, `@here`, 사용자 멘션과 역할 멘션이 알림을 보내지 못하도록 깨뜨립니다.
- `fmt.mentioned_users(text)`, `fmt.mentioned_roles(text)`, `fmt.mentioned_channels(text)`는 텍스트에서 멘션된 ID를 반환합니다.

이스케이프는 하나의 방어층일 뿐입니다. 텍스트 내용과 상관없이 무엇이 알림을 보낼 수 있는지는 [`allowed_mentions`](responses.md#allowed-mentions)가 결정합니다.

## 색상 { #colors }

`bostie.color`에는 Discord의 브랜드 색상이 `int`로 들어 있습니다. `BLURPLE`, `GREEN`, `YELLOW`, `FUCHSIA`, `RED`입니다. `color.rgb(red, green, blue)`는 0부터 255까지의 값 세 개를, `color.hsv(hue, saturation, value)`는 0부터 1까지의 값 세 개를 받고, `color.parse("#5865F2")`는 `#`이나 `0x`가 붙었거나 붙지 않은 16진수 여섯 자리를 읽습니다. 임베드의 `color`, 컨테이너의 `accent_color`, 역할이 이 값을 받습니다.

## CDN URL { #cdn-urls }

`bostie.cdn`은 Discord가 저장한 이미지의 URL을 만듭니다. 각 함수는 모델에서 가져온 ID와 이미지 해시를 받고, 추가로 16부터 4096까지의 2의 거듭제곱인 `size`와 `"png"`, `"jpg"`, `"jpeg"`, `"webp"`(기본값), `"gif"` 중 하나인 `format`을 받습니다. 해시가 `a_`로 시작하는 애니메이션 이미지는 WebP나 GIF에서 애니메이션이 유지됩니다. 정지 이미지에 `"gif"`를 지정하거나 목록에 없는 크기를 지정하면 `ValueError`가 발생합니다. 이미지를 내려받으려면 [REST 가이드](rest.md#downloading-files)에서 설명하듯 `await bot.http.read(url)`을 사용합니다.

| 함수 | 이미지 |
|---|---|
| `display_avatar(user)` | 사용자의 아바타. 아바타가 없으면 기본 아바타 |
| `user_avatar(user_id, avatar)`, `default_avatar(user_id)` | 사용자 자신의 아바타 또는 기본 아바타 |
| `user_banner(user_id, banner)`, `avatar_decoration(asset)` | 사용자의 배너와 아바타 장식 |
| `member_avatar(guild_id, user_id, avatar)`, `member_banner(...)` | 멤버의 서버별 아바타와 배너 |
| `guild_icon`, `guild_splash`, `guild_discovery_splash`, `guild_banner` | `guild_id`와 해시로 만드는 서버 이미지 |
| `guild_tag_badge(guild_id, badge)` | 서버 태그의 배지 |
| `role_icon(role_id, icon)` | 역할 아이콘 |
| `emoji(emoji_id, animated=False)` | 사용자 지정 이모지 |
| `sticker(sticker_id, format_type)` | 형식 유형에 따라 PNG, Lottie JSON, GIF로 된 스티커 |
| `sticker_pack_banner(banner_asset_id)` | 스티커 팩의 배너 |
| `scheduled_event_cover(event_id, image)` | 예약 이벤트의 커버 이미지 |
| `application_icon`, `application_cover`, `application_asset`, `store_page_asset` | 앱의 아이콘, 커버, Rich Presence 에셋, 상점 이미지 |
| `team_icon(team_id, icon)` | 팀 아이콘 |
| `soundboard_sound(sound_id)` | 사운드보드 사운드의 오디오 |
