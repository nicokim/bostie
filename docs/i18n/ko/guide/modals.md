---
source: 606da1a817f7cceca11df3edf6dda3b5da72000b
---

# 모달

![양식을 채워 보내는 Bostie](../assets/mascot/bostie-modals.webp){ align=right width="180" loading=lazy }

모달은 팝업 양식입니다. 명령어나 컴포넌트가 모달로 응답하고, 사용자가 제출하면 새 인터랙션이 도착하며 모달 라우트가 이를 처리합니다.

```python
--8<-- "docs/snippets/modals/application_form.py"
```

`ui.modal(custom_id, title, *components)`는 1~45자의 제목과 컴포넌트 1~5개를 받습니다. 컴포넌트는 `ui.label(...)` 필드와 `ui.text(...)` 문단입니다. `await ctx.modal(...)`이 모달을 엽니다. 모달은 인터랙션의 첫 번째 응답이어야 하며, 그 뒤로는 그 인터랙션에 다른 응답을 할 수 없습니다.

`@bot.on_modal(template)`은 [`on_component`](components.md#routes)가 클릭을 라우팅하는 것과 같은 방식으로 제출을 라우팅합니다. `{name}`과 `{name:int}` 자리 표시자를 쓰는 템플릿, `route.id(...)`, `ephemeral`·`auto_defer`·`checks` 설정이 모두 같습니다.

## 필드 { #fields }

`ui.label(label, component, description=None)`은 입력 하나 위에 최대 45자의 라벨과 최대 100자의 설명을 붙입니다.

| 입력 | 빌더 | 읽는 방법 |
|---|---|---|
| 텍스트 상자 | `ui.text_input(custom_id, style=TextInputStyle.SHORT)` | `ctx.text(custom_id)`, `str` |
| 옵션 선택 메뉴 | `ui.string_select(custom_id, options)` | `ctx.values(custom_id)` |
| 사용자, 역할, 둘 다, 또는 채널 선택 메뉴 | `ui.user_select`, `ui.role_select`, `ui.mentionable_select`, `ui.channel_select` | `ctx.users`, `ctx.members`, `ctx.roles`, `ctx.channels`, 또는 `ctx.ids` |
| 파일 업로드 | `ui.file_upload(custom_id)` | `ctx.attachments(custom_id)`, 업로드된 `Attachment` 객체 |
| 2~10개 중 하나 선택 | `ui.radio_group(custom_id, options)` | `ctx.choice(custom_id)`, 값 또는 `None` |
| 1~10개 중 여러 개 선택 | `ui.checkbox_group(custom_id, options)` | `ctx.values(custom_id)` |
| 예/아니요 체크박스 하나 | `ui.checkbox(custom_id, default=False)` | `ctx.checked(custom_id)`, `bool` |

체크박스를 제외한 모든 입력은 `required`를 받으며 기본값은 `True`이고, `min_values=0`을 쓰려면 `required=False`가 필요합니다. `text_input`은 여러 줄 입력을 위한 `style=TextInputStyle.PARAGRAPH`, `placeholder`, 미리 채워 둘 `value`, `min_length`(0~4000), `max_length`(1~4000)를 받습니다. `file_upload`는 최대 10개 파일까지의 `min_values`와 `max_values`, 그리고 `["image", ".pdf"]` 같은 `file_types`를 받습니다. 라디오와 체크박스 옵션은 `ui.group_option(label, value, description=None, default=False)`이며, `max_values`가 없는 체크박스 그룹에서는 사용자가 모든 옵션을 체크할 수 있습니다.

빌더는 `ui.ModalDict`, `ui.LabelDict`, `ui.TextInputDict`, `ui.FileUploadDict`, `ui.RadioGroupDict`, `ui.CheckboxGroupDict`, `ui.CheckboxDict`, `ui.GroupOptionDict`를 반환합니다. `ModalContext`의 읽기 메서드는 입력의 커스텀 ID를 받습니다. 모달에 없는 ID를 넘기면 `KeyError`가 발생하고, 체크박스에 `ctx.text`를 쓰는 것처럼 다른 종류의 입력을 읽으면 `TypeError`가 발생합니다.

## 모달을 연 메시지 수정하기 { #updating-the-message-that-opened-the-modal }

컴포넌트에서 연 모달은 그 컴포넌트가 붙어 있는 메시지를 알고 있으므로, 제출할 때 그 메시지를 수정할 수 있습니다.

```python
--8<-- "docs/snippets/modals/settings_modal.py"
```

`await ctx.update(...)`와 `await ctx.defer_update()`는 클릭에서와 똑같이 제출에서도 동작합니다. 명령어에서 연 모달에는 수정할 메시지가 없으므로 이 메서드들은 `InteractionResponded`를 발생시킵니다. 이때는 `respond`로 응답하세요. 응답하지 않은 제출은 `auto_defer`초 뒤에 지연되며, 메시지가 있으면 `defer_update`로 지연됩니다.

## 핸들러 안에서 제출 기다리기 { #waiting-for-the-submit-inside-a-handler }

```python
--8<-- "docs/snippets/modals/inline_modal.py"
```

`await bot.wait_for_modal(*custom_ids, check=None)`은 커스텀 ID가 `custom_ids` 중 하나인(아무것도 주지 않으면 모든 모달) 다음 제출의 `ModalContext`를 반환하며, 그 제출에 대해서는 라우트가 실행되지 않습니다. 두 사용자가 동시에 명령어를 실행해도 각자 자신의 제출을 받도록, 커스텀 ID에 인터랙션 ID 같은 고유한 값을 넣으세요. 사용자는 제출하지 않고 모달을 닫을 수 있고 Discord는 이를 봇에 알리지 않으므로, `asyncio.timeout()`으로 기다리는 시간을 제한하세요.
