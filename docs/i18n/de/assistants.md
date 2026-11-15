---
render_macros: true
source: 67296fb45686a92662fe14f10a697ef03c8c8044
---

# KI-Coding-Assistenten

![Bostie chattet, während sich eine Sprechblase mit Punkten füllt](assets/mascot/bostie-assistants.webp){ align=right width="180" loading=lazy }

Der bostie-Skill bringt einem Coding-Assistenten bei, wie ein bostie-Bot geschrieben wird: Befehle, Komponenten, Modals, Events, REST, Sprachkanäle und Tests, mit vollständigen Beispielen und den Gewohnheiten aus anderem Code, die in bostie scheitern. Assistenten, die Skills unterstützen, laden ihn, sobald eine Aufgabe bostie betrifft. Er liegt im Repository unter `docs/skills/bostie`, und die Tests von bostie prüfen die Typen jedes Beispiels darin, führen es aus und stellen sicher, dass jeder Name, den er erwähnt, existiert.

{% set expanded = true %}
{% include "callout.md" %}
{% from "links.md" import owner, name with context %}

## Selbst installieren { #installing-it-yourself }

```sh
npx skills add {{ owner }}/{{ name }} --skill bostie --yes
```

installiert den Skill im aktuellen Projekt für die Coding-Assistenten, die es dort findet; `--global` installiert ihn für alle deine Projekte, und `--agent claude-code`, `--agent codex` oder `--agent github-copilot` nur für einen einzelnen Assistenten. `npx skills update` aktualisiert ihn.

### Als Plugin { #as-a-plugin }

Manche Assistenten installieren den Skill auch als Plugin, für alle deine Projekte, und aktualisieren ihn zusammen mit ihren anderen Plugins:

| Assistent | Installation |
|---|---|
| Claude Code | `/plugin marketplace add {{ owner }}/{{ name }}`, dann `/plugin install bostie@bostie` |
| Codex CLI | `codex plugin marketplace add {{ owner }}/{{ name }}`, dann `codex plugin add bostie@bostie` |
| GitHub Copilot CLI | `copilot plugin marketplace add {{ owner }}/{{ name }}`, dann `copilot plugin install bostie@bostie` |
| VS Code mit GitHub Copilot | **Chat: Install Plugin from Source** in der Befehlspalette, mit `{{ owner }}/{{ name }}` |
| Devin | `devin plugins install {{ owner }}/{{ name }}#docs` |
| Factory Droid | `droid plugin marketplace add {{ owner }}/{{ name }}`, dann `droid plugin install bostie@bostie --scope user` |
| Qwen Code | `qwen extensions install {{ owner }}/{{ name }}:bostie` |

### Von Hand { #by-hand }

Kopiere den Ordner `docs/skills/bostie` des Repositorys in den Skill-Ordner deines Assistenten im Projekt:

| Assistent | Ordner |
|---|---|
| Claude Code | `.claude/skills/bostie` |
| Codex, Cursor, GitHub Copilot, Gemini CLI, OpenCode | `.agents/skills/bostie` |
| Windsurf | `.windsurf/skills/bostie` |

## Den Assistenten dazu bringen, ihn zu nutzen { #making-the-assistant-use-it }

Ein Assistent entscheidet selbst, wann er einen Skill lädt, und in veröffentlichten Auswertungen tat er das oft nicht. Eine Zeile in der Anweisungsdatei, die er zu Beginn jeder Sitzung liest, etwa `AGENTS.md`, brachte ihn in fast jedem Durchlauf dazu, den Skill zu laden. Deshalb fügt der Prompt eine solche Zeile hinzu:

```text
Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.
```

Der Skill folgt dem Hauptbranch von bostie. Nutzt dein Projekt ein älteres bostie, weist der Skill den Assistenten an, jeden Namen mit dem installierten Paket abzugleichen, das vollständig typisiert ist.
