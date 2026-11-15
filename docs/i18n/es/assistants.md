---
render_macros: true
source: 67296fb45686a92662fe14f10a697ef03c8c8044
---

# Asistentes de programación con IA

![Bostie chatea mientras un globo de diálogo se llena de puntos](assets/mascot/bostie-assistants.webp){ align=right width="180" loading=lazy }

La skill de bostie le enseña a un asistente de programación cómo se escribe un bot con bostie: comandos, componentes, modales, eventos, REST, voz y pruebas, con ejemplos completos y los hábitos de otro código que fallan en bostie. Los asistentes compatibles con skills la cargan cuando una tarea tiene que ver con bostie. Vive en el repositorio, en `docs/skills/bostie`, y las pruebas de bostie comprueban los tipos de cada ejemplo que contiene, lo ejecutan y verifican que exista cada nombre que menciona.

{% set expanded = true %}
{% include "callout.md" %}
{% from "links.md" import owner, name with context %}

## Instalarla tú mismo { #installing-it-yourself }

```sh
npx skills add {{ owner }}/{{ name }} --skill bostie --yes
```

instala la skill en el proyecto actual para los asistentes de programación que encuentre ahí; `--global` la instala para todos tus proyectos, y `--agent claude-code`, `--agent codex` o `--agent github-copilot` solo para un asistente. `npx skills update` la actualiza.

### Como plugin { #as-a-plugin }

Algunos asistentes también instalan la skill como plugin, para todos tus proyectos, y la actualizan junto con sus otros plugins:

| Asistente | Instalación |
|---|---|
| Claude Code | `/plugin marketplace add {{ owner }}/{{ name }}` y luego `/plugin install bostie@bostie` |
| Codex CLI | `codex plugin marketplace add {{ owner }}/{{ name }}` y luego `codex plugin add bostie@bostie` |
| GitHub Copilot CLI | `copilot plugin marketplace add {{ owner }}/{{ name }}` y luego `copilot plugin install bostie@bostie` |
| VS Code con GitHub Copilot | **Chat: Install Plugin from Source** en la paleta de comandos, con `{{ owner }}/{{ name }}` |
| Devin | `devin plugins install {{ owner }}/{{ name }}#docs` |
| Factory Droid | `droid plugin marketplace add {{ owner }}/{{ name }}` y luego `droid plugin install bostie@bostie --scope user` |
| Qwen Code | `qwen extensions install {{ owner }}/{{ name }}:bostie` |

### A mano { #by-hand }

Copia la carpeta `docs/skills/bostie` del repositorio en la carpeta de skills del proyecto que usa tu asistente:

| Asistente | Carpeta |
|---|---|
| Claude Code | `.claude/skills/bostie` |
| Codex, Cursor, GitHub Copilot, Gemini CLI, OpenCode | `.agents/skills/bostie` |
| Windsurf | `.windsurf/skills/bostie` |

## Hacer que el asistente la use { #making-the-assistant-use-it }

Un asistente decide por su cuenta cuándo cargar una skill, y en evaluaciones publicadas a menudo no lo hizo. Una línea en el archivo de instrucciones que lee al comienzo de cada sesión, como `AGENTS.md`, hizo que cargara la skill en casi todas las ejecuciones, así que el prompt agrega una:

```text
Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.
```

La skill sigue la rama principal de bostie. Cuando tu proyecto usa una versión anterior de bostie, la skill le indica al asistente que compruebe cada nombre contra el paquete instalado, que está completamente tipado.
