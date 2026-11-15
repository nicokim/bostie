---
render_macros: true
source: 67296fb45686a92662fe14f10a697ef03c8c8044
---

# ИИ-ассистенты для программирования

![Бости переписывается, пока облачко с репликой заполняется точками](assets/mascot/bostie-assistants.webp){ align=right width="180" loading=lazy }

Навык bostie учит ассистента для программирования тому, как пишется бот на bostie: команды, компоненты, модальные окна, события, REST, голос и тесты, с полными примерами и привычками из другого кода, которые в bostie не работают. Ассистенты, поддерживающие навыки, загружают его, когда задача касается bostie. Он лежит в репозитории в `docs/skills/bostie`, а тесты bostie проверяют типы и запускают каждый пример из него и проверяют, что каждое упомянутое в нём имя существует.

{% set expanded = true %}
{% include "callout.md" %}
{% from "links.md" import owner, name with context %}

## Самостоятельная установка { #installing-it-yourself }

```sh
npx skills add {{ owner }}/{{ name }} --skill bostie --yes
```

устанавливает навык в текущий проект для тех ассистентов, которых найдёт в нём; `--global` устанавливает его для всех ваших проектов, а `--agent claude-code`, `--agent codex` или `--agent github-copilot` — только для одного ассистента. `npx skills update` его обновляет.

### Как плагин { #as-a-plugin }

Некоторые ассистенты также устанавливают навык как плагин — для всех ваших проектов — и обновляют его вместе с остальными плагинами:

| Ассистент | Установка |
|---|---|
| Claude Code | `/plugin marketplace add {{ owner }}/{{ name }}`, затем `/plugin install bostie@bostie` |
| Codex CLI | `codex plugin marketplace add {{ owner }}/{{ name }}`, затем `codex plugin add bostie@bostie` |
| GitHub Copilot CLI | `copilot plugin marketplace add {{ owner }}/{{ name }}`, затем `copilot plugin install bostie@bostie` |
| VS Code с GitHub Copilot | **Chat: Install Plugin from Source** в палитре команд, с `{{ owner }}/{{ name }}` |
| Devin | `devin plugins install {{ owner }}/{{ name }}#docs` |
| Factory Droid | `droid plugin marketplace add {{ owner }}/{{ name }}`, затем `droid plugin install bostie@bostie --scope user` |
| Qwen Code | `qwen extensions install {{ owner }}/{{ name }}:bostie` |

### Вручную { #by-hand }

Скопируйте папку `docs/skills/bostie` из репозитория в папку навыков проекта для вашего ассистента:

| Ассистент | Папка |
|---|---|
| Claude Code | `.claude/skills/bostie` |
| Codex, Cursor, GitHub Copilot, Gemini CLI, OpenCode | `.agents/skills/bostie` |
| Windsurf | `.windsurf/skills/bostie` |

## Как заставить ассистента его использовать { #making-the-assistant-use-it }

Ассистент сам решает, когда загружать навык, и в опубликованных оценках часто этого не делал. Строка в файле инструкций, который он читает в начале каждой сессии, например `AGENTS.md`, заставляла его загружать навык почти в каждом запуске, поэтому промпт добавляет такую строку:

```text
Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.
```

Навык следует за основной веткой bostie. Если ваш проект использует более старую версию bostie, навык велит ассистенту сверять каждое имя с установленным пакетом, который полностью типизирован.
