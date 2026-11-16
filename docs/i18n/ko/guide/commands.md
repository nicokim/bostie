---
source: 620651abbb35486bdf47aba0669128b8bb1d0ad6
description: "Python 타입 힌트로 Discord 슬래시 명령어를 정의합니다. 옵션 타입, 선택지, 범위, 자동 완성, 컨버터, 하위 명령어 그룹, 제한을 다룹니다."
---

# 슬래시 명령어

![말풍선에 슬래시 명령어를 쓰는 Bostie](../assets/mascot/bostie-commands.webp){ align=right width="180" loading=lazy }

슬래시 명령어는 `@bot.command`로 데코레이트한 비동기 함수입니다. 첫 번째 매개변수는 [`Context`](responses.md)를 받고, 그 뒤의 매개변수는 모두 명령어의 옵션이 됩니다.

```python
--8<-- "docs/snippets/commands/option_types.py"
```

이 명령어는 `/report`입니다. 이름은 함수 이름이고 설명은 docstring의 첫 줄입니다. `@bot.command(name=..., description=...)`로 대신 지정할 수도 있으며, 둘 다 없는 명령어의 설명은 `…`가 됩니다. `@bot.command`와 `@bot.command(...)` 모두 봇에서도, [라우터](routers.md)에서도 쓸 수 있습니다.

## 옵션 타입 { #option-types }

매개변수의 타입 힌트가 옵션 타입을 정하고, 핸들러는 이미 변환된 값을 받습니다.

| 힌트 | Discord 옵션 | 핸들러가 받는 값 |
|---|---|---|
| `str` | 문자열 | 입력한 텍스트 |
| `int` | 정수 | `int` |
| `float` | 숫자 | `float`(사용자가 정수를 입력해도 마찬가지) |
| `bool` | 불리언 | `bool` |
| `User` | 사용자 | `User` |
| `ResolvedMember` | 사용자 | 멤버와 그 `user`, 서버에서만 |
| `Role` | 역할 | `Role` |
| `PartialChannel` | 채널 | 인터랙션이 해석한 그대로의 채널 |
| `Attachment` | 첨부 파일 | 업로드된 파일의 `Attachment` |
| `User \| Role` 또는 `ResolvedMember \| Role` | 멘션 가능 대상 | 사용자가 고른 쪽 |
| `Literal[...]` 또는 `Enum` | 선택지가 있는 문자열, 정수, 숫자 | 리터럴 값 또는 열거형 멤버 |

표에 없는 힌트를 쓰면 데코레이터가 실행될 때 `CommandDefinitionError`가 발생합니다. 채널 옵션의 힌트는 `Channel`이 아니라 `PartialChannel`이고, 멤버의 힌트는 `Member`가 아니라 `ResolvedMember`입니다. 인터랙션에는 이런 부분 객체가 담겨 오기 때문이며, 에러 메시지에서도 이를 알려 줍니다.

## 필수 옵션과 선택 옵션 { #required-and-optional }

기본값이 없는 옵션은 필수입니다. 기본값을 주거나 힌트에 `| None`을 붙이면 선택 옵션이 되고, 사용자가 생략한 선택 옵션은 기본값이나 `None`을 받습니다. Discord는 필수 옵션이 먼저 오기를 요구하므로, bostie는 매개변수 순서와 관계없이 명령어를 만들 때 필수 옵션을 앞으로 정렬합니다.

## 설명 { #descriptions }

`Annotated` 안의 문자열이 옵션의 설명이 됩니다(`Annotated[str, "What happened"]`). 설명을 docstring에 쓸 수도 있습니다.

```python
--8<-- "docs/snippets/commands/docstring_options.py"
```

bostie는 위와 같은 `Args:` 섹션과, 대시로 밑줄을 친 `Parameters` 섹션을 읽습니다. 설명은 여러 줄에 걸쳐 써도 됩니다. `Annotated` 안의 문자열이 `Option(description=)`보다 우선하고, `Option(description=)`은 docstring보다 우선합니다. 어디에도 설명이 없는 옵션은 `…`를 설명으로 받습니다.

## 범위와 옵션 설정 { #ranges-and-option-settings }

`Range[int, 1, 10]`은 1부터 10까지(양 끝 포함)의 `int` 옵션입니다. `Range`에는 `float`도 쓸 수 있고, `str`이면 범위가 길이를 뜻합니다(`Range[str, 1, 2000]`). 나머지 설정은 모두 `Annotated` 안의 `Option(...)`으로 지정하며, 힌트에서 유추되는 내용보다 우선합니다.

```python
--8<-- "docs/snippets/commands/option_settings.py"
```

| `Option` 필드 | 적용 대상 | 의미 |
|---|---|---|
| `name` | 전체 | Discord에 표시되는 옵션 이름. 매개변수는 Python 이름을 그대로 유지합니다 |
| `description` | 전체 | `Annotated`에 문자열이 없을 때 쓰는 설명 |
| `name_localizations`, `description_localizations` | 전체 | 로케일별 텍스트(예: `{"fr": "salon"}`) |
| `min`, `max` | 정수, 숫자 | 양 끝을 포함하는 범위, ±2⁵³ 이내 |
| `min_length`, `max_length` | 문자열 | 길이 범위, 각각 0~6000과 1~6000 |
| `channel_types` | 채널 | 사용자가 고를 수 있는 채널 타입 |
| `file_types` | 첨부 파일 | `image`, `video`, `audio` 또는 `.pdf` 같은 확장자, 최대 10개 |
| `choices` | 문자열, 정수, 숫자 | 고정된 선택지. 아래를 참고하세요 |
| `autocomplete` | 문자열, 정수, 숫자 | 사용자가 입력하는 동안 값을 제안하는 함수 |
| `extra` | 전체 | 옵션의 JSON에 병합되는 원시 필드. bostie가 아직 모델링하지 않은 필드에 씁니다 |

해당하지 않는 옵션 타입에 필드를 지정하면 `CommandDefinitionError`가 발생합니다.

## 선택지 { #choices }

`Literal`이나 `Enum`은 고정된 목록을 제공합니다. `Literal` 선택지의 이름은 그 값이고, `Enum` 선택지의 이름은 멤버 이름이며 멤버의 값을 보냅니다. 같은 값을 같은 순서로 나열하기만 하면 `Option(choices=[...])`로 이름을 바꾸거나 현지화할 수 있습니다.

```python
--8<-- "docs/snippets/commands/option_choices.py"
```

`/order`의 핸들러는 `drink`로 `"tea"`, `"coffee"`, `"water"` 중 하나를, `size`로 `Size` 멤버를, `milk`로 선택된 `Choice`의 값을 받습니다. 힌트가 그냥 `str`, `int`, `float`이면 `Option(choices=[...])`만으로 선택지를 정의합니다. 각 선택지는 `Choice(name, value)`이거나, 값 자체가 이름이 되는 값입니다. 선택지 값은 옵션의 타입과 같아야 하고, `Literal`이나 `Enum`의 값은 모두 `str`이거나, 모두 `int`이거나, 모두 `float`여야 합니다. 옵션 하나에는 선택지가 최대 25개이고, 선택지 이름은 1~100자, 문자열 값은 최대 100자입니다.

## 자동 완성 { #autocomplete }

`Option(autocomplete=function)`은 사용자가 입력하는 동안 `function`에 제안을 요청합니다. 이 함수는 `AutocompleteContext`와 지금까지 입력된 텍스트를 받고, 값이나 `Choice` 객체를 반환합니다.

```python
--8<-- "docs/snippets/commands/autocomplete_options.py"
```

`ctx.options`에는 지금까지 채워진 옵션이 옵션 이름별로 들어 있으며, 변환되는 옵션은 핸들러가 받을 때처럼 변환된 상태입니다. 입력 중인 옵션은 텍스트 그대로 남습니다. bostie는 처음 25개의 제안을 보내고, 100자가 넘는 이름은 자르며, 이름이 비었거나 값의 타입이 틀렸거나 문자열 값이 100자를 넘는 제안은 버리고 각각 경고를 남깁니다. 한 옵션에 `choices`와 `autocomplete`를 함께 쓸 수는 없습니다. 함수가 예외를 일으키면 에러는 [에러 훅](checks.md#error-hooks)으로 가고, Discord에는 빈 목록이 전달됩니다.

## 컨버터 { #converters }

`Annotated[T, Convert(function)]`은 핸들러가 실행되기 전에 옵션 값을 `T`로 바꿉니다. `function(ctx, value)`는 비동기여도 되며, `value` 매개변수의 타입 힌트가 `Range`와 `Annotated` 설명까지 포함해 Discord 옵션 타입을 정합니다.

```python
--8<-- "docs/snippets/commands/convert_options.py"
```

컨버터는 사용자가 입력한 옵션에만 실행됩니다. 생략된 선택 옵션은 변환되지 않은 기본값을 받습니다. 컨버터에서 발생한 예외는 핸들러에서 발생한 예외처럼 에러 훅으로 갑니다.

## 하위 명령어와 그룹 { #subcommands-and-groups }

`bot.group(name, description)`은 하위 명령어로 이루어진 명령어를 등록하고, `group.group(...)`은 그 안에 하위 명령어 그룹을 추가합니다. 그룹은 한 단계까지만 중첩되며, 그룹 하나에는 최대 25개 항목이 들어갑니다.

```python
--8<-- "docs/snippets/commands/command_groups.py"
```

이 코드는 `/roles give`와 `/roles color set`을 정의합니다. `@group.command(name=...)`를 쓰면 하위 명령어 이름을 함수 이름과 다르게 지을 수 있습니다. `ctx.command_name`은 실행된 전체 이름(예: `"roles color set"`)이고, 명령어 객체에서는 `command.qualified_name`이 같은 값입니다.

최상위 그룹은 `name`, `ephemeral`, `auto_defer`를 제외한 명령어 설정을 받고, 그 설정은 모든 하위 명령어에 적용됩니다. Discord에서 `default_permissions`, `nsfw`, `guilds`, `contexts`, `integration_types`는 최상위 명령어에만 있기 때문입니다. 중첩된 그룹은 두 현지화 필드와 `checks`, `extra`를 받습니다. 하위 명령어는 `name`, `description`, 두 현지화 필드, `ephemeral`, `auto_defer`, `checks`, `extra`를 받습니다. 그룹의 체크는 하위 명령어의 체크보다 먼저 실행됩니다.

## 명령어 설정 { #command-settings }

```python
--8<-- "docs/snippets/commands/command_settings.py"
```

| 설정 | 의미 |
|---|---|
| `name`, `description` | 함수에서 가져오는 대신 쓸 이름과 설명 |
| `name_localizations`, `description_localizations` | 로케일별 텍스트. 번역 함수는 [명령어 등록](registration.md#translations)을 참고하세요 |
| `ephemeral` | 호출에서 따로 지정하지 않는 한 응답이 사용자 본인에게만 보입니다 |
| `auto_defer` | 응답하지 않은 사용을 지연(defer)할 때까지의 초. `None`이면 끕니다([인터랙션에 응답하기](responses.md#deferring)) |
| `default_permissions` | 멤버가 명령어를 보고 쓰는 데 필요한 권한. 서버 관리자는 누가 쓸 수 있는지 바꿀 수 있습니다 |
| `nsfw` | 연령 제한 채널에서만 표시 |
| `guilds` | 전역 대신 명령어를 등록할 서버 ID |
| `contexts` | 전역 명령어를 쓸 수 있는 곳: 서버, 봇과의 개인 메시지(DM), 비공개 채널 |
| `integration_types` | 명령어를 포함하는 설치 방식: 서버 설치, 사용자 설치 |
| `checks` | 핸들러보다 먼저 실행되는 체크([체크](checks.md)) |
| `extra` | 명령어의 JSON에 병합되는 원시 필드 |

`ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts`, `integration_types`는 [라우터](routers.md#defaults)의 모든 명령어에 한꺼번에 지정할 수도 있고, `contexts`, `integration_types`, `auto_defer`는 봇의 모든 명령어에 지정할 수도 있습니다.

## 제한 { #limits }

데코레이터는 Discord가 거부할 내용을 미리 검사하고, 함수와 매개변수 이름을 담아 `CommandDefinitionError`를 발생시킵니다.

- 이름은 1~32자의 문자, 숫자, `-`, `_`로 이루어지며 대문자는 쓸 수 없습니다. 사용자 명령어와 메시지 명령어의 이름에는 대문자와 공백도 쓸 수 있습니다.
- 설명은 1~100자입니다.
- 명령어 하나에는 옵션이 최대 25개, 옵션 하나에는 선택지가 최대 25개입니다.
- `*args`, `**kwargs`, 위치 전용 옵션, 타입 힌트가 없는 옵션은 거부됩니다.

명령어의 이름, 설명, 선택지 값을 모두 합쳐 Discord가 허용하는 8000자를 넘으면 에러가 아니라 경고로 알려 줍니다. 동기화는 한 범위에 슬래시 명령어가 100개, 사용자 명령어가 15개, 메시지 명령어가 15개를 넘으면 경고합니다.

## 다음 단계 { #next }

- [사용자, 메시지, 진입점 명령어](context-menus.md)
- [명령어 등록](registration.md): 서버 하나 또는 전역으로 Discord에 등록하고 번역하는 방법
- [인터랙션에 응답하기](responses.md)
