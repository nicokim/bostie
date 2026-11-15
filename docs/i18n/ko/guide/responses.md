---
source: cbf9d472ef0e0c0f610b3352754ad6482b93f093
---

# 인터랙션에 응답하기

슬래시 명령어, 컨텍스트 메뉴 명령어, 버튼, 선택 메뉴, 모달 제출은 모두 인터랙션이며, Discord는 앱이 여기에 응답하기를 기다립니다. 명령어 핸들러는 `Context`를, 컴포넌트 핸들러는 `ComponentContext`를, 모달 핸들러는 `ModalContext`를 받으며, 이들은 아래의 응답 메서드를 공통으로 가집니다.

## 컨텍스트가 알고 있는 것 { #what-a-context-knows }

| 속성 | 값 |
|---|---|
| `ctx.user` | 인터랙션을 일으킨 `User`(서버에서든 개인 메시지에서든) |
| `ctx.member` | 서버에서는 그 사용자의 `Member`, 그 밖에서는 `None` |
| `ctx.guild_id`, `ctx.channel_id` | 인터랙션이 일어난 곳. 개인 메시지에서는 `guild_id`가 `None`입니다 |
| `ctx.channel` | 인터랙션에 담겨 온 `PartialChannel` |
| `ctx.locale` | 사용자의 클라이언트 언어 |
| `ctx.interaction` | 인터랙션 모델 전체. `app_permissions`나 `guild_locale` 같은 그 밖의 정보에 씁니다 |
| `ctx.bot` | 봇 |
| `ctx.voice` | 이 서버와 사용자에 대한 [음성](../voice/index.md) |
| `ctx.command_name` | `Context`에서 하위 명령어까지 포함한 명령어 이름(예: `"roles color set"`) |

## 응답, 수정, 후속 메시지 { #answer-edit-follow-up }

```python
--8<-- "docs/snippets/responses/answer_flow.py"
```

`ctx.respond(...)`는 첫 번째 응답을 보냅니다. 다시 호출하면 후속 메시지를 보내므로, 핸들러는 필요한 만큼 몇 번이든 호출할 수 있습니다. 반환값은 Discord가 만든 메시지인 `WebhookMessage`입니다.

- `ctx.edit(**parts)`는 첫 번째 응답을, `ctx.edit(message_id, **parts)`는 후속 메시지를 수정합니다.
- `ctx.followup(...)`은 항상 새 메시지를 보냅니다. 단, `defer()` 직후에는 지연된 응답을 채웁니다.
- `ctx.fetch()`와 `ctx.fetch(message_id)`는 응답이나 후속 메시지를 Discord에 있는 현재 상태 그대로 반환합니다.
- `ctx.delete()`와 `ctx.delete(message_id)`는 이를 삭제합니다.

`respond`와 `followup`은 `content`와 [메시지 내용](messages.md)에 나오는 요소들, 즉 `embeds`, `components`, `files`, `allowed_mentions`, `flags`, `tts`, `poll`에 더해 `ephemeral`을 받습니다. 수정할 때도 같은 요소를 키워드로 받습니다. 생략한 요소는 그대로 남고, `None`을 넘기면 지워집니다.

## 스스로 삭제되는 메시지 { #messages-that-delete-themselves }

```python
--8<-- "docs/snippets/responses/short_lived.py"
```

`respond`, `followup`, `bot.rest.messages.create`에 `delete_after=`를 넘기면, 보낸 메시지를 그 초만큼 지난 뒤에 삭제합니다. 봇은 삭제를 백그라운드에서 실행하고 종료할 때 취소하므로, 봇이 멈춘 뒤로 예정된 메시지는 남습니다. 인터랙션의 메시지는 그 토큰으로 삭제하는데, 에페메랄 메시지에도 통하지만 인터랙션 후 15분이 지나면 쓸 수 없습니다. 그보다 늦게 예정된 삭제는 경고를 로그에 남기고 실패합니다. 누군가 이미 삭제한 메시지는 조용히 건너뜁니다.

## 에페메랄 응답 { #ephemeral-answers }

호출에 `ephemeral=True`를 넘기면 그 메시지는 사용자 본인에게만 보입니다. 명령어, 컴포넌트 라우트, 모달 라우트, 라우터에 `ephemeral=True`를 지정하면 그 응답들의 기본값이 되며, 호출에서 `ephemeral=False`를 넘겨 바꿀 수도 있습니다. 이미 있는 메시지는 에페메랄과 공개 사이를 오갈 수 없습니다. 이를 요청하는 수정은 경고를 한 번 로그에 남기고 메시지를 그대로 둡니다.

## 응답 지연 { #deferring }

첫 번째 응답은 3초 안에 Discord에 도착해야 합니다. `await ctx.defer()`를 호출하면 응답 대신 "thinking…"이 표시되고, 핸들러는 그때부터 15분 안에 `respond`하면 됩니다. `defer(ephemeral=True)`는 이어질 응답을 에페메랄로 만듭니다.

```python
--8<-- "docs/snippets/responses/deferring.py"
```

bostie는 스스로 응답을 지연하기도 합니다. 도착한 지 `auto_defer`초가 지나도록 응답하지 않은 사용은 지연되며, 기본값은 2.5초입니다. 메시지에서 열린 컴포넌트나 모달은 `defer_update`로 지연되는데, 이 경우 `update`가 메시지를 수정할 때까지 메시지가 그대로 유지됩니다. `auto_defer`는 봇, 라우터, 명령어, 라우트에 지정하며, `None`을 지정하면 그 범위에서 꺼집니다. 타이머는 게이트웨이가 인터랙션을 전달한 시점부터 계산되므로, 이벤트 루프가 막히면 핸들러는 늦어지지만 마감 시간은 늦어지지 않습니다. 핸들러가 1초 넘게 늦게 시작하면 bostie는 경고를 로그에 남깁니다.

## 모달과 활동 { #modals-and-activities }

`await ctx.modal(ui.modal(...))`은 팝업 양식으로 응답합니다. [모달](modals.md)을 참고하세요. `await ctx.launch_activity()`는 앱의 활동(Activity)을 열고 그 인스턴스의 ID를 반환합니다. 둘 다 첫 번째 응답이어야 하며, 모달을 띄운 뒤에는 그 인터랙션에 더 이상 응답할 수 없습니다. 모달 제출은 새로운 인터랙션입니다.

## 규칙과 규칙을 어겼을 때의 에러 { #the-rules-and-the-errors-for-breaking-them }

각 호출은 먼저 인터랙션의 상태를 확인합니다.

| 호출 | 응답 전 | `defer()` 후 | `defer_update()` 후 | 응답 후 |
|---|---|---|---|---|
| `respond` | 응답합니다 | 응답을 채웁니다 | 후속 메시지를 보냅니다 | 후속 메시지를 보냅니다 |
| `defer` | 지연합니다 | 아무것도 하지 않습니다 | 아무것도 하지 않습니다 | `InteractionResponded` |
| `edit`, `delete` | `InteractionNotAnswered` | 응답을 수정, 삭제합니다 | 메시지를 수정, 삭제합니다 | 응답을 수정, 삭제합니다 |
| `followup` | `InteractionNotAnswered` | 응답을 채웁니다 | 후속 메시지를 보냅니다 | 후속 메시지를 보냅니다 |
| `update` | 메시지를 수정합니다 | `InteractionResponded` | 메시지를 수정합니다 | `InteractionResponded` |
| `modal`, `launch_activity` | 응답합니다 | `InteractionResponded` | `InteractionResponded` | `InteractionResponded` |

`update`와 `defer_update`는 컴포넌트 컨텍스트와 모달 컨텍스트에만 있습니다. 모달을 띄운 뒤의 호출은 모두 `InteractionResponded`를 일으킵니다.

인터랙션 토큰은 15분 동안 유효합니다. 그 뒤에 하는 호출이나, Discord가 토큰이 유효하지 않다고 알린 뒤에 하는 호출은 아무것도 보내지 않고 `InteractionExpired`를 일으킵니다.

응답하지 않고 반환하는 핸들러는 사용자에게 "The application did not respond"를 남기며, bostie는 핸들러 이름을 담은 경고를 로그에 남깁니다. 지연한 뒤 끝내 응답하지 않은 핸들러는 토큰이 만료될 때까지 "thinking…"을 남기며, 이때도 경고가 남습니다. 핸들러가 응답하기 전에 예외를 일으키면, [에러 훅](checks.md#error-hooks)이 응답하지 않는 한 bostie가 "Something went wrong."으로 응답합니다. 이 응답은 핸들러가 지연하지 않았다면 에페메랄입니다.

사용자에게만 설치된 앱의 인터랙션에는 Discord가 후속 메시지를 5개까지 허용하며, bostie는 여섯 번째에서 경고합니다.

## 허용 멘션 { #allowed-mentions }

기본적으로 응답은 사용자와 역할에게 알림을 보낼 수 있지만 `@everyone`이나 `@here`에는 보낼 수 없습니다. `Bot(allowed_mentions=...)`의 기본값이 `AllowedMentions(parse=("users", "roles"))`이기 때문입니다. 메시지와 함께 넘긴 `allowed_mentions`는 자신이 지정한 멘션 종류만 바꾸므로, `AllowedMentions(users=(123,))`는 사용자 알림만 제한하고 역할 설정은 그대로 둡니다. `AllowedMentions(parse=())`는 모든 멘션의 알림을 끄고, `replied_user=False`는 답장이 원래 메시지 작성자에게 알림을 보내지 않게 합니다.
