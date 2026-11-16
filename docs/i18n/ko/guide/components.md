---
source: d624adfef5ab8dd6ffcb780c8ed7ca6459e83b3f
description: "bostie.ui로 버튼, 선택 메뉴, Components V2 레이아웃을 만들고, 봇을 재시작해도 동작하는 핸들러로 커스텀 ID를 라우팅합니다."
---

# 컴포넌트

![커다란 버튼을 누르는 Bostie](../assets/mascot/bostie-components.webp){ align=right width="180" loading=lazy }

컴포넌트는 메시지에 들어가는 버튼, 선택 메뉴, 레이아웃 블록입니다. `bostie.ui`는 컴포넌트를 Discord의 JSON 형태를 따르는 일반 딕셔너리로 만들고, 만드는 시점에 Discord의 제한을 검사합니다. 컴포넌트가 사용되면 `@bot.on_component`로 등록한 핸들러가 응답합니다.

컴포넌트는 `bostie.ui`로 만듭니다. `bostie.Button`이나 `bostie.ActionRow` 같은 컴포넌트 모델은 디코딩한 메시지의 `components`에 담기는 객체입니다. `components=`는 이 모델도 받으므로, 메시지의 컴포넌트를 다시 만들지 않고 `msgspec.structs.replace`로 수정해 그대로 다시 보낼 수 있습니다. 다만 모델은 Discord의 제한을 검사하지 않습니다.

## 레이아웃 { #layout }

```python
--8<-- "docs/snippets/components/layout.py"
```

이것이 Components V2입니다. [메시지 내용](messages.md#components-v2)에서 설명하듯 컴포넌트가 메시지 전체를 이루며, `content` 대신 `ui.text`를 씁니다.

| 빌더 | 표시하는 것 |
|---|---|
| `ui.text(markdown)` | Discord 마크다운을 쓸 수 있는 텍스트 |
| `ui.section(*texts, accessory=...)` | 텍스트 1~3개와 그 옆의 버튼 또는 썸네일 |
| `ui.thumbnail(url, description=None, spoiler=False)` | 작은 이미지. 섹션의 accessory로만 사용 |
| `ui.media_gallery(*items)` | 이미지나 동영상 1~10개. 각각 `ui.gallery_item(url, description=None, spoiler=False)` |
| `ui.file(filename, spoiler=False)` | 메시지와 함께 그 이름으로 업로드한 파일 |
| `ui.separator(divider=True, spacing=1)` | 컴포넌트 사이의 여백. `spacing=2`면 넓게, `divider=False`가 아니면 구분선 포함 |
| `ui.container(*components, accent_color=None, spoiler=False)` | 행, 텍스트, 섹션, 갤러리, 파일, 구분선을 감싸는 상자. 테두리에 색이 들어감 |
| `ui.row(*components)` | 버튼 최대 5개, 또는 선택 메뉴 1개 |

URL에 `attachment://name`을 쓰면 메시지와 함께 업로드한 파일을 표시합니다. 모든 빌더는 선택적인 숫자 컴포넌트 ID를 지정하는 `id=`를 받으며, `ui.raw(dict)`는 `ui`에 아직 빌더가 없는 컴포넌트를 그대로 넘깁니다. 항목이 너무 많거나, 라벨이 너무 길거나, 버튼이 있는 행 안에 선택 메뉴를 넣으면 만드는 시점에 `ValueError`가 발생합니다. 각 빌더는 `ui.ButtonDict`, `ui.RowDict`, `ui.TextDict`, `ui.SectionDict`, `ui.ThumbnailDict`, `ui.MediaGalleryDict`, `ui.GalleryItemDict`, `ui.FileDict`, `ui.SeparatorDict`, `ui.ContainerDict`, `ui.StringSelectDict`, `ui.SelectOptionDict`, `ui.EntitySelectDict`, `ui.SelectDefaultDict` 같은 타입이 지정된 딕셔너리를 반환하고, `ui.raw`는 `ui.RawComponent`를 반환합니다. 아래의 `ballot`처럼 직접 만든 헬퍼에 이 타입으로 어노테이션을 붙이세요.

## 버튼 { #buttons }

- `ui.button(label, custom_id=..., style=ButtonStyle.SECONDARY)`는 클릭되면 봇에 `custom_id`를 보냅니다. `style`은 `PRIMARY`, `SECONDARY`, `SUCCESS`, `DANGER` 중 하나입니다.
- `ui.link_button(label, url)`은 URL을 열며, 봇에는 전달되지 않습니다.
- `ui.premium_button(sku_id)`는 앱의 SKU를 판매하며, 봇에는 전달되지 않습니다.

버튼은 `emoji=`를 받습니다. 유니코드 이모지, Discord가 표시하는 형식으로 쓴 사용자 지정 이모지(`<:name:id>`, 애니메이션이면 `<a:name:id>`. `fmt.emoji`가 만드는 형식입니다), 또는 `Emoji`나 `PartialEmoji` 모델을 넘길 수 있습니다. `PartialEmoji.parse(text)`는 이런 문자열을 읽습니다. 버튼은 `disabled=True`도 받습니다. 라벨은 최대 80자, 커스텀 ID는 1~100자입니다.

## 라우트 { #routes }

라우트는 템플릿에 맞는 모든 커스텀 ID를 처리하는 핸들러입니다.

```python
--8<-- "docs/snippets/components/routes.py"
```

`"vote:{side:int}:{topic}"`은 `vote:0:Lunch at noon`과 일치합니다. `{name}`은 줄 바꿈이 없는 문자 하나 이상을, `{name:int}`는 음수를 포함한 정수를 캡처합니다. 캡처한 값은 키워드 인자로 핸들러에 전달되며, `int`는 변환되어 전달됩니다. 템플릿의 두 자리 표시자 사이에는 리터럴 텍스트가 있어야 하며, 자리 표시자가 없는 템플릿은 커스텀 ID와 정확히 같을 때만 일치합니다.

`vote.id(side=0, topic=topic)`은 라우트 자체에서 커스텀 ID를 만들므로, 템플릿은 한 번만 쓰면 됩니다. ID가 Discord의 100자 제한보다 길어지거나, 파싱했을 때 같은 값이 나오지 않으면 `ValueError`를 발생시킵니다.

상태는 Python 객체가 아니라 커스텀 ID에 들어 있으므로, 지난주에 보낸 버튼도 라우트가 등록되어 있는 한 재시작 뒤에도 동작합니다. `@bot.on_component`는 명령어처럼 `ephemeral`, `auto_defer`, `checks`를 받으며, `await vote(ctx, side=0, topic="x")`처럼 라우트를 호출하면 핸들러가 호출됩니다.

어떤 라우트와도 일치하지 않는 컴포넌트 사용에는 응답하지 않습니다. `InteractionCreate`를 수신하지 않는 한, Discord는 "This interaction failed"를 표시하고 bostie는 경고를 로그에 남깁니다.

## 컴포넌트에 응답하기 { #answering-a-component }

`ComponentContext`는 명령어 컨텍스트의 응답 메서드에 더해 다음 메서드를 가집니다.

- `await ctx.update(**parts)`는 컴포넌트가 붙어 있는 메시지를 수정합니다. 생략한 구성 요소는 그대로 남고, `None`을 넘기면 지워집니다.
- `await ctx.defer_update()`는 눈에 보이는 응답 없이 사용을 확인합니다. 이후 15분 안에 `update`로 메시지를 수정할 수 있습니다.
- `await ctx.modal(ui.modal(...))`은 [모달](modals.md)을 엽니다.

`respond`는 명령어와 마찬가지로 새 메시지를 보냅니다. `auto_defer`초 동안 응답하지 않은 사용은 `defer_update`로 지연됩니다.

## 선택 메뉴 { #select-menus }

```python
--8<-- "docs/snippets/components/selects.py"
```

| 빌더 | 사용자가 고르는 것 | 선택한 값을 읽는 방법 |
|---|---|---|
| `ui.string_select(custom_id, options)` | `ui.select_option(label, value)`로 만든 옵션 | `ctx.values`, 값 목록 |
| `ui.user_select(custom_id)` | 사용자 | `ctx.users`, 서버에서는 `ctx.members`도 |
| `ui.role_select(custom_id)` | 역할 | `ctx.roles` |
| `ui.mentionable_select(custom_id)` | 사용자와 역할 | `ctx.users`, `ctx.members`, `ctx.roles` |
| `ui.channel_select(custom_id, channel_types=...)` | 지정한 유형의 채널만 | `ctx.channels` |

`ctx.ids`에는 모든 엔티티 선택 메뉴에서 고른 ID가 들어 있습니다. 사용자 선택 메뉴에서 `ctx.values`를 읽는 것처럼 맞지 않는 종류를 읽으면 `TypeError`가 발생합니다.

모든 선택 메뉴는 `placeholder`(최대 150자), `min_values`와 `max_values`(기본값 1, 최대 25), `disabled`를 받습니다. 문자열 선택 메뉴에는 옵션이 1~25개 있습니다. `select_option`은 `description`, 버튼과 같은 방식의 `emoji`, 그리고 선택된 상태로 표시하는 `default=True`를 받습니다. 엔티티 선택 메뉴는 선택된 상태로 표시할 ID인 `defaults`를 받고, 멘션 가능 선택 메뉴는 `default_users`와 `default_roles`를 받습니다. `required`는 [모달](modals.md)에서만 의미가 있습니다.

## 페이지 { #pages }

`ui.pager(route, page, count)`는 `{page:int}` 자리 표시자가 있는 라우트에 대해 버튼 세 개로 된 행을 반환합니다. 이전 버튼, 비활성화된 `page / count` 라벨, 다음 버튼입니다. 첫 페이지는 0이며, 추가 키워드 인자는 라우트의 다른 자리 표시자를 채웁니다. 페이지 번호가 커스텀 ID에 들어 있으므로 재시작 뒤에도 페이지 넘김이 동작합니다. [컴포넌트 예제](../examples.md#components)의 `/tour` 명령어가 이 기능을 사용합니다.

## 핸들러 안에서 클릭 기다리기 { #waiting-for-a-click-inside-a-handler }

명령어는 라우트 없이 컴포넌트 사용 한 번을 기다릴 수 있습니다.

```python
--8<-- "docs/snippets/components/confirm.py"
```

`await bot.wait_for_component(*custom_ids, check=None)`은 커스텀 ID가 `custom_ids` 중 하나이고(아무것도 주지 않으면 모든 컴포넌트) `check`가 참을 반환하는 다음 사용의 `ComponentContext`를 반환합니다. 이렇게 반환된 사용에 대해서는 라우트가 실행되지 않습니다. 무한정 기다리므로 `asyncio.timeout()`으로 시간을 제한하세요. `check`에서 발생한 예외는 여기서 다시 발생합니다. 반환하는 컨텍스트에 쓸 `ephemeral`과 `auto_defer`도 받습니다.
