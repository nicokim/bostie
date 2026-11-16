---
source: b7efeeb83216fd56b3819a874abdb0bfb6a2a072
description: "bostie가 앱 명령어를 Discord와 동기화하는 방식입니다. 전역과 서버 범위, 개발용 테스트 서버, 명령어 멘션, 번역, 동기화 에러를 다룹니다."
---

# 명령어 등록

Discord는 앱이 등록한 명령어만 표시합니다. bostie가 이 일을 대신 해 줍니다. 봇이 시작할 때 코드에 정의한 명령어를 Discord에 있는 명령어와 비교하고, 다른 점이 있을 때만 보냅니다.

## 시작할 때의 동기화 { #the-sync-at-startup }

`bot.start()`와 `bot.run()`은 게이트웨이에 연결하기 전에 동기화합니다. bostie는 범위(전역 또는 서버 하나)마다 등록된 명령어를 현지화 정보와 함께 가져와 필드 단위로 비교하고, 다르면 그 범위의 명령어를 요청 한 번으로 교체합니다. 로그에는 `commands up to date (global)` 또는 `commands synced (global): added [...], removed [...], changed [...]`가 남습니다.

Discord에는 있지만 더 이상 코드에 정의하지 않은 명령어는 삭제됩니다. 단, 한 번도 정의하지 않는 타입의 명령어는 예외입니다. `@bot.entry_point`가 없는 봇은 Discord가 활동(Activity)을 위해 만든 진입점 명령어를 그대로 둡니다.

`Bot(auto_sync=False)`는 동기화를 건너뜁니다. `await bot.sync_commands()`는 변경 여부와 관계없이 모든 범위의 명령어를 지금 교체하고, 범위마다 `SyncResult`를 반환합니다. `SyncResult`에는 `added`, `removed`, `changed`된 이름과, Discord가 슬래시 명령어에 부여한 `ids`가 들어 있습니다.

## 범위 { #scopes }

`guilds`가 없는 명령어는 전역 명령어입니다. 앱을 설치한 모든 서버에서 동작하고, `contexts`가 허용하면 개인 메시지(DM)에서도 동작합니다. `guilds=[...]`를 지정하면 해당 서버에만 등록합니다.

봇을 개발하는 동안에는 모든 명령어를 테스트 서버에 등록하세요.

```python
--8<-- "docs/snippets/commands/command_sync.py"
```

`Bot(dev_guilds=[...])`는 자체 `guilds`가 없는 모든 명령어를 전역 대신 해당 서버에 등록합니다. 진입점 명령어는 항상 전역이므로, 동기화는 이 명령어를 빼고 경고합니다.

### 여러 범위에서 같은 이름 쓰기 { #one-name-in-several-scopes }

```python
--8<-- "docs/snippets/commands/guild_variants.py"
```

같은 서버에 두 개가 겹치지 않는다면 명령어끼리 이름을 공유할 수 있습니다. 예를 들어 전역 `/rules`와 특정 서버용 `/rules`를 함께 두거나, 서버마다 `/rules`를 하나씩 둘 수 있습니다. 각 명령어는 자기 범위에 등록되며, 인터랙션은 그 서버에 등록된 명령어를 실행하고, 없으면 전역 명령어를 실행합니다. 해당 서버에서 Discord는 전역 명령어와 서버 명령어를 모두 목록에 보여 줍니다. `dev_guilds`를 쓰는 동안에는 서버 자체 명령어가 그 서버에서 전역 명령어를 대신합니다. 이름이 같은 두 명령어의 서버가 겹치거나 둘 다 전역이면, 두 번째 명령어를 등록할 때 `CommandDefinitionError`가 발생합니다. 사용자 명령어와 메시지 명령어도 같은 규칙을 따릅니다.

동기화는 명령어가 있는 범위만 건드립니다. `dev_guilds`를 없애면 테스트 서버의 명령어는 새로 등록한 전역 명령어와 함께 남습니다. `await bot.rest.commands.bulk_overwrite_guild(bot.application_id, guild_id, [])`로 지울 수 있습니다.

## 명령어를 쓸 수 있는 곳 { #where-commands-can-be-used }

`contexts`와 `integration_types`는 전역 명령어에 적용됩니다. 명령어, 라우터, 봇에 지정합니다.

- `contexts`: `InteractionContextType.GUILD`, `BOT_DM`(봇과의 개인 메시지), `PRIVATE_CHANNEL`(사용자 설치 앱의 경우 그 밖의 개인 메시지와 그룹 메시지).
- `integration_types`: `ApplicationIntegrationType.GUILD_INSTALL`과 `USER_INSTALL`.

`Bot(contexts=[...], integration_types=[...])`는 따로 지정하지 않은 모든 전역 명령어의 기본값이 됩니다. 어디에도 지정하지 않으면 bostie는 두 필드를 모두 보내지 않고 Discord가 기본값을 적용합니다. `integration_types`의 기본값은 앱에 설정된 설치 컨텍스트입니다. 지정했던 설정을 없애는 것도 변경으로 간주하므로, 다음 동기화에서 명령어는 다시 Discord의 기본값을 받습니다.

## 명령어 멘션 { #command-mentions }

`await bot.command_mention("roles color set")`은 `</roles color set:ID>`를 반환하며, Discord는 이를 클릭할 수 있는 명령어로 표시합니다. 마지막 동기화에서 받은 ID를 쓰거나, 범위마다 한 번씩 Discord에 물어봅니다. `guild_id=`를 넘기면 그 서버에 등록된 명령어가 같은 이름의 전역 명령어보다 우선합니다. 알 수 없는 이름이면 `LookupError`가, 봇이 시작되기 전에 호출하면 `StateError`가 발생합니다. 이미 ID가 있다면 `fmt.command(name, command_id)`로 같은 텍스트를 만들 수 있습니다.

## 번역 { #translations }

명령어, 그룹, 하위 명령어, `Option`, `Choice`의 `name_localizations`와 `description_localizations`는 텍스트 하나를 번역합니다. 번역 함수를 쓰면 이 모두를 한꺼번에 번역합니다.

```python
--8<-- "docs/snippets/commands/command_translations.py"
```

`Bot(translate=function)`은 동기화하는 동안 모든 명령어, 그룹, 하위 명령어, 옵션의 이름과 설명, 그리고 모든 선택지 이름에 대해 `Locale`마다 한 번씩 `function(text, locale)`을 호출합니다. `None`을 반환하거나 텍스트를 그대로 반환하면 그 로케일에는 아무것도 추가하지 않으며, 직접 작성한 현지화가 함수보다 우선합니다. 핸들러에서는 사용자의 언어를 `ctx.locale`로, 서버의 언어를 `ctx.interaction.guild_locale`로 확인할 수 있습니다.

## Discord가 명령어를 거부할 때 { #when-discord-refuses-the-commands }

Discord가 동기화를 400으로 거부하면 `CommandSyncError`가 발생합니다. 에러 메시지에는 문제마다 한 줄씩 명령어, 옵션 경로, Discord의 메시지가 담기고, `error.problems`에는 이 문제들이 `SyncProblem` 객체로 들어 있습니다. 대부분의 문제는 더 일찍 잡힙니다. 데코레이터가 실행될 때 이름, 설명, 제한을 검사하기 때문입니다.
