---
render_macros: true
source: dc795d93f8d81e572155a4937bfd0da9add197bf
description: "Discord 게이트웨이, HTTP, DAVE 음성을 Rust로 처리하는 bostie로 슬래시 명령어와 음성 봇을 타입이 지정된 비동기 Python으로 씁니다."
---

<div class="bostie-hero" markdown>
<div class="bostie-hero__copy" markdown>

# bostie

bostie는 게이트웨이, HTTP 클라이언트, 음성 엔진이 Rust로 동작하는 Python용 Discord 라이브러리입니다. 봇은 타입이 지정된 비동기 Python으로 작성합니다.

</div>

![두 팔을 벌려 반갑게 맞이하는 보스턴 테리어 Bostie](assets/mascot/bostie-welcoming.webp){ .bostie-hero__mascot width="200" }

</div>

!!! warning "프리알파"
    bostie의 API는 아직 바뀔 수 있습니다. 현재 슬래시 명령어, 사용자 명령어, 메시지 명령어, 컴포넌트, 모달, 게이트웨이 이벤트, 캐시, REST, 웹후크, HTTP 인터랙션, OAuth2, 음성 재생과 수신이 동작합니다.

```python
--8<-- "docs/snippets/home/readme_example.py"
```

`/play`는 명령어를 실행한 사용자가 있는 음성 채널에 들어가 URL을 스트리밍하고, Stop 버튼과 함께 응답합니다. 버튼의 커스텀 ID에는 서버 ID가 들어 있고 `@bot.on_component`가 이를 다시 `int`로 파싱하므로, 봇을 재시작한 뒤에도 버튼이 계속 동작합니다. `bot.run()`은 `BOSTIE_TOKEN` 환경 변수에서 토큰을 읽고, 명령어가 바뀌었으면 Discord에 보냅니다.

{% include "callout.md" %}

## 설계 목표 { #what-it-is-built-for }

### 음성 { #voice }

Discord는 2026년 3월 1일부터 종단 간 암호화된 통화([DAVE](https://docs.discord.com/developers/topics/voice-connections))만 허용합니다. bostie의 음성 엔진은 GIL 밖의 Rust 스레드에서 20 ms 클록, 디코딩, 믹싱, Opus 인코딩, 전송 암호화, DAVE를 처리합니다. Python은 각 싱크의 버퍼만 채워 두면 되며, 기본값은 오디오 0.2초 분량입니다. bostie는 파일, URL, HLS 스트림, 직접 만든 PCM이나 Opus를 재생하고, 여러 소스를 믹싱하며, 화자별로 따로 수신하고, 통화를 녹음합니다.

### Discord가 바뀌어도 살아남는 봇 { #bots-that-survive-discords-changes }

알 수 없는 필드, 열거형 값, 컴포넌트 타입, 인터랙션 타입이 와도 디코딩이 깨지지 않습니다. 모든 이벤트와, 페이로드 전체에서 디코딩한 모든 모델은 원시 JSON을 보관합니다. bostie가 아직 모델링하지 않은 기능은 `bot.http`와 원시 이벤트 리스너로 다룰 수 있습니다.

### Discord 문서와 일치하는 타입 { #types-that-match-discords-documentation }

Discord가 항상 보내는 필드는 절대 `None`이 아니며, 부분 객체에는 별도의 클래스가 있습니다. 명령어 옵션에서 해석된 멤버는 `ResolvedMember`이고, 여기에는 `deaf`나 `mute`가 없습니다.

### 시그니처로 만드는 명령어 { #commands-from-signatures }

옵션, 선택지, 범위, 자동 완성은 타입 힌트에서 만들어집니다. 이름, 설명, 옵션 개수, 선택지가 Discord의 제한을 지키는지는 Discord가 동기화를 거부할 때가 아니라 데코레이터가 실행될 때 검사합니다.

### Discord 없이 하는 테스트 { #tests-without-discord }

`bostie.testing.FakeDiscord`는 실제 봇을 그대로 연결해 실행할 수 있는 로컬 게이트웨이 겸 REST API입니다.

## 다음 단계 { #where-to-go-next }

- bostie와 빌드에 필요한 도구를 [설치](install.md)합니다.
- [빠른 시작](quickstart.md)에서 첫 봇을 작성합니다.
- 가이드는 [슬래시 명령어](guide/commands.md)부터 [테스트](guide/testing.md)까지 페이지마다 기능 하나씩을 설명합니다.
- [음성](voice/index.md) 섹션은 연결, 재생, 원시 오디오, 수신을 다룹니다.
- 레퍼런스에는 모든 [`Bot` 옵션](reference/bot-options.md), 모든 [이벤트](reference/event-list.md), 그리고 [API](reference/api/bot.md)가 정리되어 있습니다.
