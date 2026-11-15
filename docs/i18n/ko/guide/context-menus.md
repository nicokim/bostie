---
source: a9ef813b389718cd5d31ae958953b39416d1a494
---

# 사용자, 메시지, 진입점 명령어

Discord에는 슬래시 명령어 말고도 사용자나 메시지의 컨텍스트 메뉴에서 실행하는 명령어와, 앱의 활동(Activity)을 여는 진입점 명령어가 있습니다.

## 사용자 명령어와 메시지 명령어 { #user-and-message-commands }

```python
--8<-- "docs/snippets/commands/context_menus.py"
```

`@bot.user_command(name)`과 `@bot.message_command(name)`은 컨텍스트와 대상을 받는 함수를 등록합니다. 이름은 메뉴에 표시되는 텍스트로, 1~32자이며 슬래시 명령어 이름과 달리 대문자와 공백을 쓸 수 있습니다. 이 명령어들에는 설명과 옵션이 없습니다.

대상의 타입 힌트가 핸들러가 받을 값을 정합니다.

| 명령어 | 힌트 | 핸들러가 받는 값 |
|---|---|---|
| 사용자 | `User` | 사용자 |
| 사용자 | `ResolvedMember` | 멤버와 그 `user`, 서버에서만 |
| 사용자 | `User \| ResolvedMember` | 서버에서는 멤버, 그 밖에서는 사용자 |
| 메시지 | `Message` | 사용자나 봇이 보낸 메시지 |
| 메시지 | `WebhookMessage` | 웹후크가 보낸 메시지(인터랙션 응답 포함) |
| 메시지 | `Message \| WebhookMessage` | 둘 중 하나 |

대상이 힌트의 타입으로 해석되지 않으면 `OptionConversionError`가 발생하고, 이 에러는 [에러 훅](checks.md#error-hooks)으로 갑니다. 명령어가 둘 중 한쪽에만 의미가 있는 경우가 아니라면 메시지 대상의 힌트는 `Message | WebhookMessage`로 지정하세요.

이 명령어들은 `description`, `description_localizations`, 옵션 설정을 제외한 슬래시 명령어 설정을 받습니다. 즉 `name_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `guilds`, `contexts`, `integration_types`, `checks`, `extra`입니다. Discord는 범위 하나에 사용자 명령어 15개, 메시지 명령어 15개까지 허용하며, 이를 넘으면 동기화가 경고합니다.

## 진입점 명령어 { #the-entry-point-command }

활동(Activity)이 있는 앱에는 진입점 명령어가 하나 있으며, 앱 실행기(App Launcher)에 표시됩니다. `@bot.entry_point(name)`은 컨텍스트만 받는 함수로 이 명령어를 등록합니다.

```python
--8<-- "docs/snippets/commands/entry_point.py"
```

핸들러는 다른 명령어와 똑같이 응답합니다. `ctx.launch_activity()`는 활동을 열고 그 인스턴스의 ID를 반환하며, 대신 `ctx.respond(...)`로 메시지를 보내 응답할 수도 있습니다. Developer Portal에서 활동을 켜지 않은 앱의 진입점 명령어는 Discord가 거부합니다.

진입점 명령어는 항상 전역입니다. `dev_guilds`를 설정하면 동기화는 이 명령어를 빼고 경고합니다. Discord에 기본 진입점 명령어가 다른 이름으로 이미 있으면, 동기화는 그 명령어의 ID를 유지하므로 Discord가 둘 다 두지 않고 기존 명령어를 교체합니다. `launch_activity()`는 컴포넌트 컨텍스트와 모달 컨텍스트에도 있지만, 반드시 첫 번째 응답이어야 합니다.

`entry_point`는 `description`, `name_localizations`, `description_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts`, `integration_types`, `checks`, `extra`를 받습니다. 활동의 나머지 부분은 [활동, 수익화, Social SDK](platform.md#activities)에서 다룹니다.
