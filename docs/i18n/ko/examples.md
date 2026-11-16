---
source: f54db29cb26a01ab63ca1c726a1815c968b1cf3a
description: "완성된 bostie 예제 봇입니다. 슬래시 명령어, Components V2, 모달, 이벤트, 음악 큐, 음성 녹음, 웹후크, 연결된 역할을 다룹니다."
---

# 예제

저장소의 `examples/` 폴더에 있는 파일들입니다. 테스트 스위트는 이 파일들을 타입 검사하고 하나씩 불러오는데, 불러오면서 각 파일의 봇을 만들고 모든 명령어 정의를 검사합니다. 각 파일은 `BOSTIE_TOKEN`을 설정한 상태에서 `python examples/commands.py`처럼 실행합니다.

## 명령어 { #commands }

타입이 지정된 옵션, 그룹, 자동 완성, 사용자 명령어와 메시지 명령어, 체크, 에러 훅, gettext 카탈로그를 이용한 번역.

```python
--8<-- "examples/commands.py"
```

## 컴포넌트 { #components }

Components V2 둘러보기: 페이지 넘김, 컨테이너 색을 바꾸는 선택 메뉴, 확인 클릭을 기다린 뒤 실행하는 추방.

```python
--8<-- "examples/components.py"
```

## 모달 { #modals }

버튼으로 열고 제출할 때 읽는 모달, 그리고 명령어가 제출을 기다리는 모달.

```python
--8<-- "examples/modals.py"
```

## 이벤트 { #events }

어노테이션으로 지정한 리스너, 유니언, 봇이 직접 정의한 이벤트, `ListenerError` 리스너, `wait_for`로 만든 숫자 맞히기 게임.

```python
--8<-- "examples/events.py"
```

## 수명 주기 { #lifecycle }

`Starting` 리스너에서 열고 `Stopped` 리스너에서 닫는 데이터베이스, 그리고 백그라운드 태스크 두 개.

```python
--8<-- "examples/lifecycle.py"
```

## 음악 { #music }

서버마다 하나씩 있는 큐와 건너뛰기, 반복, 나가기.

```python
--8<-- "examples/music.py"
```

## 녹음 { #recording }

`/stop`을 실행할 때까지 화자마다 Ogg Opus 파일 하나씩 녹음.

```python
--8<-- "examples/recording.py"
```

## 활동 { #activity }

앱의 활동(Activity)을 여는 진입점 명령어와 슬래시 명령어.

```python
--8<-- "examples/activity.py"
```

## HTTP 인터랙션 { #http-interactions }

`ASGIApp`이 HTTP로 제공하는 슬래시 명령어.

```python
--8<-- "examples/http_interactions.py"
```

## REST 스크립트 { #rest-script }

게이트웨이 없이 쓰는 REST API.

```python
--8<-- "examples/rest_script.py"
```

## 웹 앱 { #web-app }

웹 앱의 lifespan 안에서 시작하는 봇과, 봇의 REST 클라이언트로 메시지를 보내는 라우트.

```python
--8<-- "examples/web_app.py"
```

## 웹후크 { #webhook }

비동기 코드와 일반 코드에서 웹후크 URL로 메시지 보내기.

```python
--8<-- "examples/webhook.py"
```

## 연결된 역할 { #linked-roles }

연결된 역할 인증 페이지: OAuth2 로그인, 코드 교환, 사용자의 역할 연결.

```python
--8<-- "examples/linked_roles.py"
```

## 모듈식 봇 { #modular-bot }

이름으로 모듈을 포함하고, 명령어로 그 모듈을 다시 불러오는 봇.

```python
--8<-- "examples/modular/bot.py"
```

```python
--8<-- "examples/modular/greetings.py"
```
