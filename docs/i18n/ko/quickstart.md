---
render_macros: true
source: 879f90299466f4c668efe8cb4ef1edb576a11bc2
description: "Discord 애플리케이션을 만들어 서버에 추가하고, 슬래시 명령어와 타입이 지정된 옵션을 가진 첫 Python 봇을 bostie로 단계별로 실행합니다."
---

# 빠른 시작

![터미널에 코드를 입력하는 Bostie](assets/mascot/bostie-quickstart.webp){ align=right width="180" loading=lazy }

이 페이지에서는 Discord 애플리케이션을 만들어 서버에 추가하고, 슬래시 명령어 하나를 가진 봇을 실행합니다. bostie가 [설치](install.md)되어 있다고 가정합니다.

{% include "callout.md" %}

## 애플리케이션 만들기 { #create-the-application }

1. [Developer Portal](https://discord.com/developers/applications)을 열고 애플리케이션을 만듭니다.
2. **Bot** 페이지에서 토큰을 재설정하고 복사합니다. 토큰이 있으면 누구든 봇을 조종할 수 있으므로, 토큰을 코드와 저장소에 넣지 마세요.
3. **Installation** 페이지에서 서버 설치(guild install)에 `applications.commands`와 `bot` 스코프를 추가합니다. 그런 다음 설치 링크를 열어, 직접 관리하는 서버에 앱을 추가합니다.

## 봇 작성하기 { #write-the-bot }

다음 코드를 `bot.py`로 저장합니다.

```python
--8<-- "docs/snippets/quickstart/hello_bot.py"
```

`@bot.command`는 함수를 슬래시 명령어 `/hello`로 바꿉니다. 명령어 이름은 함수 이름이고, 설명은 docstring의 첫 줄입니다. `ctx`는 명령어 한 번의 사용을 나타내는 [`Context`](guide/responses.md)로, 누가 어디서 실행했는지와 응답에 쓰는 메서드를 담고 있습니다.

## 실행하기 { #run-it }

`BOSTIE_TOKEN` 환경 변수로 토큰을 넘기고 파일을 실행합니다.

```sh
export BOSTIE_TOKEN="your token"
python bot.py
```

`bot.run()`은 로깅을 INFO 레벨로 설정하고 연결한 다음, 코드에 정의한 명령어를 Discord에 등록된 명령어와 비교합니다. 처음 시작할 때는 `/hello`를 등록하고 `commands synced (global): added ['hello'], ...`를 로그에 남깁니다. 그 뒤로는 명령어가 바뀌지 않는 한 아무것도 보내지 않습니다. 서버에서 `/hello`를 입력하면 봇이 응답합니다.

Ctrl+C를 누르거나 프로세스 관리자가 SIGTERM을 보내면 봇이 깔끔하게 종료됩니다.

## 옵션 추가하기 { #add-an-option }

`ctx` 뒤의 매개변수는 명령어의 옵션이 되며, 옵션 타입은 타입 힌트로 정해집니다.

```python
--8<-- "docs/snippets/quickstart/roll_bot.py"
```

`sides`는 2부터 100까지의 정수 옵션이고 기본값은 6입니다. 그래서 Discord는 이 옵션을 선택 사항으로 표시하고, 범위를 벗어난 값은 봇에 전달되기 전에 거부합니다. 봇을 재시작하면 바뀐 명령어를 보냅니다.

## 다음 단계 { #next }

- [슬래시 명령어](guide/commands.md)에서는 모든 옵션 타입, 선택지, 자동 완성, 하위 명령어를 다룹니다.
- [인터랙션에 응답하기](guide/responses.md)에서는 `respond`, 응답 지연, 후속 메시지, 에페메랄 응답을 설명합니다.
- [명령어 등록](guide/registration.md)에서는 개발하는 동안 테스트 서버 하나에만 명령어를 등록하는 방법을 보여 줍니다.
- [봇 실행하기](guide/running.md)에서는 토큰, 로깅, 봇의 수명 주기, 다른 프로그램 안에서 봇을 실행하는 방법을 다룹니다.
