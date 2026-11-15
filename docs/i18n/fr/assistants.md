---
render_macros: true
source: 67296fb45686a92662fe14f10a697ef03c8c8044
---

# Assistants de code IA

![Bostie discute pendant qu'une bulle se remplit de points](assets/mascot/bostie-assistants.webp){ align=right width="180" loading=lazy }

La skill bostie apprend à un assistant de code comment s'écrit un bot bostie : commandes, composants, fenêtres modales, événements, REST, voix et tests, avec des exemples complets et les habitudes venues d'autre code qui échouent dans bostie. Les assistants qui prennent en charge les skills la chargent quand une tâche concerne bostie. Elle se trouve dans le dépôt, sous `docs/skills/bostie`, et les tests de bostie vérifient les types de chacun de ses exemples, les exécutent et contrôlent que chaque nom qu'elle mentionne existe.

{% set expanded = true %}
{% include "callout.md" %}
{% from "links.md" import owner, name with context %}

## L'installer vous-même { #installing-it-yourself }

```sh
npx skills add {{ owner }}/{{ name }} --skill bostie --yes
```

installe la skill dans le projet courant pour les assistants de code qu'il y trouve ; `--global` l'installe pour tous vos projets, et `--agent claude-code`, `--agent codex` ou `--agent github-copilot` pour un seul assistant. `npx skills update` la met à jour.

### Sous forme de plugin { #as-a-plugin }

Certains assistants installent aussi la skill sous forme de plugin, pour tous vos projets, et la mettent à jour avec leurs autres plugins :

| Assistant | Installation |
|---|---|
| Claude Code | `/plugin marketplace add {{ owner }}/{{ name }}`, puis `/plugin install bostie@bostie` |
| Codex CLI | `codex plugin marketplace add {{ owner }}/{{ name }}`, puis `codex plugin add bostie@bostie` |
| GitHub Copilot CLI | `copilot plugin marketplace add {{ owner }}/{{ name }}`, puis `copilot plugin install bostie@bostie` |
| VS Code avec GitHub Copilot | **Chat: Install Plugin from Source** dans la palette de commandes, avec `{{ owner }}/{{ name }}` |
| Devin | `devin plugins install {{ owner }}/{{ name }}#docs` |
| Factory Droid | `droid plugin marketplace add {{ owner }}/{{ name }}`, puis `droid plugin install bostie@bostie --scope user` |
| Qwen Code | `qwen extensions install {{ owner }}/{{ name }}:bostie` |

### À la main { #by-hand }

Copiez le dossier `docs/skills/bostie` du dépôt dans le dossier de skills du projet pour votre assistant :

| Assistant | Dossier |
|---|---|
| Claude Code | `.claude/skills/bostie` |
| Codex, Cursor, GitHub Copilot, Gemini CLI, OpenCode | `.agents/skills/bostie` |
| Windsurf | `.windsurf/skills/bostie` |

## Faire en sorte que l'assistant l'utilise { #making-the-assistant-use-it }

Un assistant décide seul du moment où il charge une skill, et dans des évaluations publiées, il ne le faisait souvent pas. Une ligne dans le fichier d'instructions qu'il lit au début de chaque session, comme `AGENTS.md`, lui a fait charger la skill dans presque toutes les exécutions ; le prompt en ajoute donc une :

```text
Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.
```

La skill suit la branche principale de bostie. Quand votre projet utilise une version plus ancienne de bostie, la skill demande à l'assistant de vérifier chaque nom dans le paquet installé, qui est entièrement typé.
