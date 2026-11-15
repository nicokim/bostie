---
render_macros: true
source: 67296fb45686a92662fe14f10a697ef03c8c8044
---

# AI 编程助手

![Bostie 在聊天，对话气泡里冒出一串省略号](assets/mascot/bostie-assistants.webp){ align=right width="180" loading=lazy }

bostie 技能会教给编程助手如何编写 bostie 机器人：命令、组件、模态框、事件、REST、语音和测试，附带完整示例，还会指出那些从其他代码沿袭而来、在 bostie 中行不通的习惯。支持技能的助手会在任务涉及 bostie 时加载它。它位于仓库中的 `docs/skills/bostie`，bostie 的测试会对其中每个示例进行类型检查并运行，同时检查其中提到的每个名称都确实存在。

{% set expanded = true %}
{% include "callout.md" %}
{% from "links.md" import owner, name with context %}

## 自行安装 { #installing-it-yourself }

```sh
npx skills add {{ owner }}/{{ name }} --skill bostie --yes
```

会为当前项目中找到的编程助手安装该技能；`--global` 会为你的所有项目安装，`--agent claude-code`、`--agent codex` 或 `--agent github-copilot` 则只为某一个助手安装。`npx skills update` 用于更新它。

### 作为插件安装 { #as-a-plugin }

有些助手也可以把技能作为插件安装，对你的所有项目生效，并随其他插件一起更新：

| 助手 | 安装方式 |
|---|---|
| Claude Code | `/plugin marketplace add {{ owner }}/{{ name }}`，然后 `/plugin install bostie@bostie` |
| Codex CLI | `codex plugin marketplace add {{ owner }}/{{ name }}`，然后 `codex plugin add bostie@bostie` |
| GitHub Copilot CLI | `copilot plugin marketplace add {{ owner }}/{{ name }}`，然后 `copilot plugin install bostie@bostie` |
| VS Code with GitHub Copilot | 在命令面板中执行 **Chat: Install Plugin from Source**，并输入 `{{ owner }}/{{ name }}` |
| Devin | `devin plugins install {{ owner }}/{{ name }}#docs` |
| Factory Droid | `droid plugin marketplace add {{ owner }}/{{ name }}`，然后 `droid plugin install bostie@bostie --scope user` |
| Qwen Code | `qwen extensions install {{ owner }}/{{ name }}:bostie` |

### 手动安装 { #by-hand }

把仓库中的 `docs/skills/bostie` 文件夹复制到项目中对应助手的技能文件夹：

| 助手 | 文件夹 |
|---|---|
| Claude Code | `.claude/skills/bostie` |
| Codex、Cursor、GitHub Copilot、Gemini CLI、OpenCode | `.agents/skills/bostie` |
| Windsurf | `.windsurf/skills/bostie` |

## 让助手用上它 { #making-the-assistant-use-it }

助手会自行决定何时加载技能，而在已公开的评测中，它们经常不加载。在助手每次会话开始时读取的指令文件（例如 `AGENTS.md`）中加一行说明，几乎每次运行都能让它加载技能，所以提示词会添加这样一行：

```text
Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.
```

该技能跟随 bostie 的主分支。如果你的项目使用的是较旧版本的 bostie，技能会让助手对照已安装的包检查每个名称，这个包带有完整的类型信息。
