---
render_macros: true
source: 67296fb45686a92662fe14f10a697ef03c8c8044
---

# Assistentes de programação com IA

![Bostie conversando enquanto um balão de fala se enche de reticências](assets/mascot/bostie-assistants.webp){ align=right width="180" loading=lazy }

A skill do bostie ensina a um assistente de programação como se escreve um bot com o bostie: comandos, componentes, modais, eventos, REST, voz e testes, com exemplos completos e os hábitos vindos de outro código que falham no bostie. Os assistentes que suportam skills a carregam quando uma tarefa envolve o bostie. Ela fica no repositório em `docs/skills/bostie`, e os testes do bostie verificam os tipos e rodam cada exemplo dela, além de conferir que cada nome que ela menciona existe.

{% set expanded = true %}
{% include "callout.md" %}
{% from "links.md" import owner, name with context %}

## Instalando você mesmo { #installing-it-yourself }

```sh
npx skills add {{ owner }}/{{ name }} --skill bostie --yes
```

instala a skill no projeto atual para os assistentes de programação que encontrar nele; `--global` a instala para todos os seus projetos, e `--agent claude-code`, `--agent codex` ou `--agent github-copilot` para um único assistente. `npx skills update` a atualiza.

### Como plugin { #as-a-plugin }

Alguns assistentes também instalam a skill como plugin, para todos os seus projetos, e a atualizam junto com os outros plugins:

| Assistente | Instalação |
|---|---|
| Claude Code | `/plugin marketplace add {{ owner }}/{{ name }}`, depois `/plugin install bostie@bostie` |
| Codex CLI | `codex plugin marketplace add {{ owner }}/{{ name }}`, depois `codex plugin add bostie@bostie` |
| GitHub Copilot CLI | `copilot plugin marketplace add {{ owner }}/{{ name }}`, depois `copilot plugin install bostie@bostie` |
| VS Code com GitHub Copilot | **Chat: Install Plugin from Source** na Command Palette, com `{{ owner }}/{{ name }}` |
| Devin | `devin plugins install {{ owner }}/{{ name }}#docs` |
| Factory Droid | `droid plugin marketplace add {{ owner }}/{{ name }}`, depois `droid plugin install bostie@bostie --scope user` |
| Qwen Code | `qwen extensions install {{ owner }}/{{ name }}:bostie` |

### Manualmente { #by-hand }

Copie a pasta `docs/skills/bostie` do repositório para a pasta de skills do projeto do seu assistente:

| Assistente | Pasta |
|---|---|
| Claude Code | `.claude/skills/bostie` |
| Codex, Cursor, GitHub Copilot, Gemini CLI, OpenCode | `.agents/skills/bostie` |
| Windsurf | `.windsurf/skills/bostie` |

## Fazendo o assistente usá-la { #making-the-assistant-use-it }

Um assistente decide sozinho quando carregar uma skill, e em avaliações publicadas muitas vezes ele não carregou. Uma linha no arquivo de instruções que ele lê no início de cada sessão, como o `AGENTS.md`, fez com que ele carregasse a skill em quase todas as execuções, então o prompt adiciona uma:

```text
Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.
```

A skill acompanha a branch principal do bostie. Quando o seu projeto usa um bostie mais antigo, a skill diz ao assistente para conferir cada nome no pacote instalado, que é totalmente tipado.
