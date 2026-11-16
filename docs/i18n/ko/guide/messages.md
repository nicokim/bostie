---
source: 16b527ff6747318f04713be223db608d76440776
description: "임베드, 파일 업로드, 투표, 답장, 전달, Components V2, 입력 중 표시, 음성 메시지까지 같은 구성 요소로 Discord 메시지를 만듭니다."
---

# 메시지 내용

메시지는 어디로 보내든 같은 구성 요소로 만듭니다. `ctx.respond`, `ctx.followup`, `ctx.edit`와 `ctx.update`, `bot.rest.messages.create`와 `edit`, 그리고 웹후크의 `execute` 모두 마찬가지입니다. 각 구성 요소는 키워드 인자이고, 생략한 구성 요소는 보내지 않습니다.

| 구성 요소 | 타입 | 참고 |
|---|---|---|
| `content` | `str` | `respond`, `followup`, `messages.create`, `execute`의 첫 번째 위치 인자 |
| `embeds` | `Embed` 객체 | 리치 임베드 최대 10개 |
| `components` | `ui` 빌더 | 버튼, 선택 메뉴, 레이아웃 컴포넌트. [컴포넌트](components.md) 참고 |
| `files` | `File` 객체 | 업로드할 파일. 아래 참고 |
| `allowed_mentions` | `AllowedMentions` | 어떤 멘션이 알림을 보낼 수 있는지. [허용 멘션](responses.md#allowed-mentions) 참고 |
| `flags` | `MessageFlags` | `SUPPRESS_EMBEDS`, `SUPPRESS_NOTIFICATIONS` 등 |
| `tts` | `bool` | 채널을 열어 둔 사람에게 내용을 소리 내어 읽어 줍니다 |
| `poll` | `PollCreateRequest` | 투표. 아래 참고 |
| `ephemeral` | `bool` | 인터랙션 응답에만 사용 |

`bot.rest.messages.create`는 `reply_to`, `message_reference`, `sticker_ids`, `nonce`, `enforce_nonce`, `shared_client_theme`도 받으며, [인터랙션 응답](responses.md#messages-that-delete-themselves)처럼 `delete_after`도 받습니다.

## 임베드와 파일 { #embeds-and-files }

```python
--8<-- "docs/snippets/responses/rich_messages.py"
```

`Embed`와 그 구성 요소(`EmbedField`, `EmbedFooter`, `EmbedAuthor`, `EmbedMedia`)는 키워드 인자로 만드는 불변(frozen) 모델이며, Discord의 JSON 형태를 따르는 일반 `dict`도 사용할 수 있습니다. `color`는 `0x5865F2` 같은 `int`를 받고, [`bostie.color`](formatting.md#colors) 헬퍼로 이 값을 만들 수 있습니다.

`File(name, data)`는 `data`를 `name`이라는 이름으로 업로드합니다. `description`은 대체 텍스트이고, `spoiler=True`는 파일을 흐리게 가리며, `title`은 Discord가 표시하는 제목을 정합니다. 임베드나 컴포넌트는 위 예제의 차트처럼 `attachment://name`으로 업로드한 파일을 가리킵니다. bostie는 크기를 검사하지 않습니다. Discord의 업로드 제한은 서버의 부스트와 사용자의 Nitro에 따라 달라집니다.

## 투표 { #polls }

`PollCreateRequest`는 `question`과 `answers`를 받으며, 둘 다 `text`를 가진 `PollMedia`이고 답변에는 선택적으로 `emoji`를 넣을 수 있습니다. `duration`은 시간 단위이고 생략하면 24이며, `allow_multiselect=True`를 주면 사용자가 답변을 여러 개 고를 수 있습니다. `bot.rest.polls.end(channel_id, message_id)`는 투표를 일찍 마감하고, `bot.rest.polls.list_answer_voters(...)`는 특정 답변을 고른 사용자를 나열하며, `PollVoteAdd`와 `PollVoteRemove` [이벤트](events.md)는 투표가 일어날 때마다 알려 줍니다.

## 수정과 첨부 파일 { #editing-and-attachments }

수정할 때는 구성 요소를 키워드로 넘기며, `attachments`도 함께 넘길 수 있습니다. 생략한 구성 요소는 그대로 남고, `None`을 넘기면 지워집니다. `ctx.edit(embeds=None)`은 임베드를 제거하고 내용은 유지합니다. 수정에 `files`나 `attachments`를 넘기면, 메시지에는 `attachments`에 `Attachment` 객체나 ID로 나열한 첨부 파일과 새 파일만 남습니다.

## Components V2 { #components-v2 }

`components`에 레이아웃 컴포넌트(`ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file`, `ui.separator`, `ui.thumbnail`)가 들어 있는 메시지는 Components V2 메시지입니다. bostie가 `IS_COMPONENTS_V2` 플래그를 설정하고, 컴포넌트가 메시지 전체가 됩니다. 이런 메시지에는 `content`, `embeds`, 스티커, 투표를 넣을 수 없으며, 넘기면 bostie가 `ValueError`를 발생시킵니다. 텍스트는 대신 `ui.text`에 넣으세요. `ui.row`에 담은 버튼과 선택 메뉴만 쓰는 경우에는 내용이 있는 기존 메시지에서도 동작합니다.

## 답장 { #replies }

```python
--8<-- "docs/snippets/responses/replies.py"
```

`reply_to`는 같은 채널에 있는 메시지의 ID를 받습니다. 메시지를 전달하려면 대신 `message_reference=MessageReference(type=MessageReferenceType.FORWARD, message_id=..., channel_id=...)`를 넘깁니다. 두 가지는 함께 쓸 수 없습니다. 서버에서 `message.content`를 읽으려면 [게이트웨이 가이드](gateway.md#intents)에서 설명하듯 특권 인텐트인 Message Content 인텐트가 필요합니다.

## 입력 중 표시 { #typing-indicator }

```python
--8<-- "docs/snippets/responses/typing_indicator.py"
```

`async with ctx.typing():`은 블록이 실행되는 동안 인터랙션의 채널에 봇이 입력 중이라고 표시합니다. Discord는 이 표시를 10초 동안 보여 주므로, bostie는 블록이 시작될 때 표시를 보내고 이후 9초마다 다시 보내며, 예외로 끝나는 경우를 포함해 블록이 끝나면 멈춥니다. `await ctx.typing()`은 한 번만 표시합니다. 인터랙션 밖에서는 `bot.rest.channels.trigger_typing_indicator(channel_id)`가 같은 일을 하며, `await`로 호출하거나 `async with`로 쓸 수 있습니다. 봇이 채널에 메시지를 보내면 표시가 바로 사라집니다.

## 음성 메시지 { #voice-messages }

```python
--8<-- "docs/snippets/responses/voice_memo.py"
```

`await voice.message(audio)`는 파일 경로나 바이트를 읽어 길이를 재고, Discord가 그리는 파형을 계산한 뒤, 바로 보낼 수 있는 `File`을 반환합니다. 형식은 이름의 확장자로 정해지며, Discord 클라이언트는 48 kHz 모노 Ogg Opus로 녹음합니다. 음성 메시지는 그 파일 하나뿐이고 다른 것은 담을 수 없습니다. 내용, 임베드, 컴포넌트, 투표, 스티커 모두 넣을 수 없습니다. bostie는 `IS_VOICE_MESSAGE` 플래그를 설정하고, 메시지에 다른 것이 있으면 `ValueError`를 발생시킵니다. 직접 만든 `File`도 `duration_secs`와 Discord가 그리는 8비트 샘플인 `waveform`이 있으면 음성 메시지가 됩니다.
