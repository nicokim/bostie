---
render_macros: true
---

# AI coding assistants

The bostie skill teaches a coding assistant how a bostie bot is written: commands, components, modals, events, REST, voice and tests, with complete examples and the habits from other code that fail in bostie. Assistants that support skills load it when a task involves bostie. It lives in the repository at `docs/skills/bostie`, and bostie's tests type-check and run every example in it and check that every name it mentions exists.

{% set expanded = true %}
{% include "callout.md" %}
{% from "links.md" import owner, name with context %}

## Installing it yourself

```sh
npx skills add {{ owner }}/{{ name }} --skill bostie --yes
```

installs the skill in the current project for the coding assistants it finds there; `--global` installs it for all your projects, and `--agent claude-code`, `--agent codex` or `--agent github-copilot` for one assistant only. `npx skills update` updates it.

### As a plugin

Some assistants also install the skill as a plugin, for all your projects, and update it with their other plugins:

| Assistant | Install |
|---|---|
| Claude Code | `/plugin marketplace add {{ owner }}/{{ name }}`, then `/plugin install bostie@bostie` |
| Codex CLI | `codex plugin marketplace add {{ owner }}/{{ name }}`, then `codex plugin add bostie@bostie` |
| GitHub Copilot CLI | `copilot plugin marketplace add {{ owner }}/{{ name }}`, then `copilot plugin install bostie@bostie` |
| VS Code with GitHub Copilot | **Chat: Install Plugin from Source** in the Command Palette, with `{{ owner }}/{{ name }}` |
| Devin | `devin plugins install {{ owner }}/{{ name }}#docs` |
| Factory Droid | `droid plugin marketplace add {{ owner }}/{{ name }}`, then `droid plugin install bostie@bostie --scope user` |
| Qwen Code | `qwen extensions install {{ owner }}/{{ name }}:bostie` |

### By hand

Copy the `docs/skills/bostie` folder of the repository into the project's skills folder for your assistant:

| Assistant | Folder |
|---|---|
| Claude Code | `.claude/skills/bostie` |
| Codex, Cursor, GitHub Copilot, Gemini CLI, OpenCode | `.agents/skills/bostie` |
| Windsurf | `.windsurf/skills/bostie` |

## Making the assistant use it

An assistant decides by itself when to load a skill, and in published evaluations it often did not. A line in the instructions file it reads at the start of every session, such as `AGENTS.md`, made it load the skill in almost every run, so the prompt adds one:

```text
Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.
```

The skill follows bostie's main branch. When your project uses an older bostie, the skill tells the assistant to check each name against the installed package, which is fully typed.
