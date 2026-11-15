---
source: 8bfaf791038898b7b25fb584ff4496c345a14fcb
---

# 모델과 열거형

bostie가 Discord에서 디코딩하는 모든 것은 모델입니다. `Message`, `Guild`, `Member`, `Interaction`을 비롯해 200개 남짓한 모델이 있으며, 모두 `bostie`에서 가져올 수 있습니다. 모델은 고정(frozen)된 `msgspec` 구조체로, JSON 바이트에서 바로 디코딩됩니다.

```python
--8<-- "docs/snippets/models/inspect_message.py"
```

## 타입은 Discord 문서를 따릅니다 { #types-follow-discords-documentation }

각 필드의 타입은 Discord 문서에 적힌 그대로입니다.

- Discord가 항상 보내는 필드에는 기본값이 없고, 빠지는 일도 없습니다. 문서에 `null`일 수 있다고 되어 있으면 `T | None`입니다.
- 문서에서 선택 사항으로 표시한 필드에는 기본값이 있습니다. 리스트는 `()`, 플래그는 빈 플래그 집합, 없으면 false를 뜻하는 불리언은 `False`, 그 밖에는 `None`입니다.
- ID는 `int`, 타임스탬프는 UTC 기준의 시간대 정보가 있는 `datetime` 객체, 색상은 `int`입니다.
- Discord가 부분 객체를 보내는 필드에는 부분 모델이 쓰입니다. `PartialUser`, `PartialMember`, `PartialChannel`, `PartialMessage`, `PartialEmoji`, `PartialApplication`, `PartialIntegration`, `PartialEntitlement`, `UnavailableGuild`가 그렇습니다. 부분 모델에는 부분 페이로드에 있는 필드만 있으므로, 없는 필드에 접근하면 실행 중에 `None`이 나오는 대신 타입 에러가 됩니다.

하나의 페이로드가 두 가지 모양을 갖는 세 곳에는 클래스가 두 개씩 있습니다.

- 웹후크가 보낸 메시지는 인터랙션 응답을 포함해 `WebhookMessage`이며, 그 `author`는 `PartialUser`입니다. 그 밖의 메시지는 작성자가 완전한 `User`인 `Message`입니다. 둘 다 받는 필드의 타입은 `Message | WebhookMessage`입니다.
- 옵션, 선택 메뉴, 명령어 대상에서 해석된 멤버는 `ResolvedMember`이며, `user`를 담고 있고 `deaf`와 `mute`는 없습니다. 게이트웨이나 REST에서 받은 `Member`에는 둘 다 있습니다.
- `message.mentions`에 있는 사용자는 `MentionedUser`입니다. 메시지가 서버에서 보내졌을 때 Discord가 덧붙이는 `member`를 가진 `User`입니다.

인터랙션은 타입에 따라 `CommandInteraction`, `ComponentInteraction`, `ModalSubmitInteraction`, `AutocompleteInteraction` 중 하나이며, 각각 타입이 지정된 `data`를 가집니다.

## 변경 내성 { #change-resilience }

Discord는 필드, 열거형 값, 컴포넌트 타입을 예고 없이 추가하며, 그때 봇이 깨지면 안 됩니다.

- bostie가 모르는 필드는 디코딩할 때 무시합니다.
- 열거형은 알 수 없는 값을 `UNKNOWN_<value>`라는 이름의 고정된 멤버에 대응시키며, 이 멤버는 그 값과 같다고 비교됩니다. `MessageType(99).name == "UNKNOWN_99"`.
- 플래그는 bostie가 모르는 비트를 `~`를 포함한 모든 연산자에서 그대로 유지합니다.
- 알 수 없는 컴포넌트 타입은 `UnknownComponent`로 디코딩됩니다. 이 객체는 원시 JSON을 보관하며, `components`에 넘기면 바뀌지 않은 채로 다시 전송됩니다. 알 수 없는 인터랙션 타입은 `UnknownInteraction`으로 디코딩되며, `InteractionCreate` 리스너에는 전달되지만 명령어나 라우트에는 전달되지 않습니다.
- `message.referenced_message`나 컴포넌트 인터랙션의 메시지처럼 중첩된 메시지는 속성을 읽을 때 비로소 디코딩되므로, 깨진 메시지가 있어도 그것을 읽는 곳에서만 실패합니다.

필수 필드가 빠진 경우처럼 이벤트 페이로드가 해당 클래스에 전혀 맞지 않으면, 한 번 로그에 남기고 `RawEvent` 리스너에만 전달합니다. 자세한 내용은 [이벤트](events.md#raw-events-and-events-bostie-does-not-model)에서 설명합니다.

## 원시 페이로드 { #raw-payloads }

페이로드 전체에서 디코딩한 모델은 그 페이로드를 보관합니다. `model.raw_json`에는 바이트가 들어 있고, `model.raw`는 이를 새 `dict`로 디코딩합니다. 메시지 작성자처럼 중첩된 모델은 `raw_json = None`입니다. 모든 이벤트에도 `raw`와 `raw_json`이 있습니다. 이를 통해 bostie가 모델링하기 전부터 Discord가 보내는 어떤 필드든 읽을 수 있습니다.

모델의 `repr`에는 `raw_json`과 비밀 값인 `token`, `access_token`, `refresh_token`, `id_token`이 나오지 않습니다.

## 모델 만들기와 복사하기 { #building-and-copying-models }

모델은 불변이며, 키워드 인자로 만듭니다. `msgspec.structs.replace(model, field=value)`는 일부 필드를 바꾼 복사본을 반환합니다. `Embed`, `AllowedMentions`, `PollCreateRequest`, `MessageReference`, `ForumTag`, `RecurrenceRule`, `AutoModerationAction`처럼 Discord에 보내는 모델은 설정하지 않은 필드를 빼고 보내므로, 디코딩한 모델은 받은 그대로 다시 전송됩니다.

## 모델의 헬퍼 { #helpers-on-models }

| 헬퍼 | 값 |
|---|---|
| `user.mention`, `member.mention`, `role.mention`, `channel.mention` | `<@123>` 같은 멘션 텍스트 |
| `user.display_name`, `member.display_name` | 전역 이름 또는 사용자명. 멤버는 별명이 우선 |
| `message.jump_url` | 메시지 링크 |
| `message.author_member` | 메시지에 멤버 정보가 있을 때, `ResolvedMember`로 표현한 작성자 |
| `role.permissions`, `overwrite.allow`, `overwrite.deny` | `Permissions` 플래그 |
| `member.permissions` | Discord가 보냈을 때, 인터랙션이 일어난 채널에서 멤버가 가진 권한 |
| `webhook.url` | 웹후크에 토큰이 있을 때, 웹후크의 URL |

ID로 만드는 멘션, 타임스탬프, CDN URL은 [서식 가이드](formatting.md)에서 다룹니다.

## 스노플레이크 { #snowflakes }

Discord ID에는 객체가 만들어진 시점이 담겨 있습니다. 스노플레이크 ID를 가진 모든 모델에는 그 시점을 UTC `datetime`으로 나타내는 `created_at`이 있습니다. `message.created_at`, `member.created_at`(계정이 만들어진 시점), `emoji.created_at`(유니코드 이모지는 `None`)이 그 예입니다. ID만 있을 때는 `bostie.snowflake_time(id)`가 같은 일을 하고, `bostie.time_snowflake(when)`는 날짜로 페이지를 넘길 수 있도록 해당 시점에 만들어진 가장 작은 ID를, `high=True`를 주면 가장 큰 ID를 반환합니다. 시간대 정보가 없는 `datetime`은 로컬 시간으로 해석합니다.

## 열거형 { #enums }

`bostie`는 Discord의 열거형과 플래그를 내보냅니다. `ChannelType`, `MessageType`, `Permissions`, `Intents`, `MessageFlags`, `Locale`, `ButtonStyle`을 비롯한 나머지는 [열거형 레퍼런스](../reference/api/enums.md)에 있습니다. 플래그는 `|`로 조합하고 `in`으로 검사합니다. `Permissions.SEND_MESSAGES in member_permissions`. `message.type.deletable`은 Discord가 해당 타입의 메시지 삭제를 허용하는지 알려 줍니다. 그룹 메시지와 통화 알림, 채널 이름과 아이콘 변경 알림, 스레드의 시작 메시지는 삭제할 수 없습니다. `JSONErrorCode`는 `HTTPException.code`가 보고하는 Discord 에러 코드에 이름을 붙입니다.
