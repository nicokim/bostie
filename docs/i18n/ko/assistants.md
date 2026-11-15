---
render_macros: true
source: 67296fb45686a92662fe14f10a697ef03c8c8044
---

# AI 코딩 어시스턴트

![말풍선이 점으로 채워지는 동안 대화하는 Bostie](assets/mascot/bostie-assistants.webp){ align=right width="180" loading=lazy }

bostie 스킬은 코딩 어시스턴트에게 bostie 봇을 작성하는 방법을 가르칩니다. 명령어, 컴포넌트, 모달, 이벤트, REST, 음성, 테스트를 완전한 예제와 함께 다루고, 다른 코드에서 익힌 습관 중 bostie에서는 통하지 않는 것도 알려 줍니다. 스킬을 지원하는 어시스턴트는 bostie가 관련된 작업에서 이 스킬을 불러옵니다. 스킬은 저장소의 `docs/skills/bostie`에 있으며, bostie의 테스트는 스킬에 담긴 모든 예제를 타입 검사하고 실행하며, 스킬이 언급하는 모든 이름이 실제로 존재하는지 확인합니다.

{% set expanded = true %}
{% include "callout.md" %}
{% from "links.md" import owner, name with context %}

## 직접 설치하기 { #installing-it-yourself }

```sh
npx skills add {{ owner }}/{{ name }} --skill bostie --yes
```

위 명령은 현재 프로젝트에서 찾은 코딩 어시스턴트용으로 스킬을 설치합니다. `--global`을 붙이면 모든 프로젝트에 설치하고, `--agent claude-code`, `--agent codex`, `--agent github-copilot`을 붙이면 해당 어시스턴트 하나에만 설치합니다. `npx skills update`로 업데이트합니다.

### 플러그인으로 설치하기 { #as-a-plugin }

일부 어시스턴트는 스킬을 플러그인으로도 설치합니다. 이 경우 모든 프로젝트에 설치되고, 다른 플러그인과 함께 업데이트됩니다.

| 어시스턴트 | 설치 |
|---|---|
| Claude Code | `/plugin marketplace add {{ owner }}/{{ name }}`, 이어서 `/plugin install bostie@bostie` |
| Codex CLI | `codex plugin marketplace add {{ owner }}/{{ name }}`, 이어서 `codex plugin add bostie@bostie` |
| GitHub Copilot CLI | `copilot plugin marketplace add {{ owner }}/{{ name }}`, 이어서 `copilot plugin install bostie@bostie` |
| VS Code with GitHub Copilot | 명령 팔레트에서 **Chat: Install Plugin from Source**를 실행하고 `{{ owner }}/{{ name }}` 입력 |
| Devin | `devin plugins install {{ owner }}/{{ name }}#docs` |
| Factory Droid | `droid plugin marketplace add {{ owner }}/{{ name }}`, 이어서 `droid plugin install bostie@bostie --scope user` |
| Qwen Code | `qwen extensions install {{ owner }}/{{ name }}:bostie` |

### 직접 복사하기 { #by-hand }

저장소의 `docs/skills/bostie` 폴더를 사용하는 어시스턴트의 프로젝트 스킬 폴더에 복사합니다.

| 어시스턴트 | 폴더 |
|---|---|
| Claude Code | `.claude/skills/bostie` |
| Codex, Cursor, GitHub Copilot, Gemini CLI, OpenCode | `.agents/skills/bostie` |
| Windsurf | `.windsurf/skills/bostie` |

## 어시스턴트가 스킬을 쓰게 하기 { #making-the-assistant-use-it }

어시스턴트는 스킬을 언제 불러올지 스스로 정하는데, 공개된 평가에서는 불러오지 않는 경우가 많았습니다. 어시스턴트가 세션을 시작할 때마다 읽는 지침 파일(예: `AGENTS.md`)에 한 줄을 넣으면 거의 모든 실행에서 스킬을 불러왔기 때문에, 프롬프트에서 다음 줄을 추가합니다.

```text
Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.
```

스킬은 bostie의 main 브랜치를 따릅니다. 프로젝트에서 이전 버전의 bostie를 사용하는 경우, 스킬은 어시스턴트에게 각 이름을 설치된 패키지와 대조해 보라고 안내합니다. 설치된 패키지에는 타입이 모두 지정되어 있습니다.
