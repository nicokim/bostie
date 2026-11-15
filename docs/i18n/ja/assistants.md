---
render_macros: true
source: 67296fb45686a92662fe14f10a697ef03c8c8044
---

# AI コーディングアシスタント

![吹き出しが点で埋まっていく中でチャットする Bostie](assets/mascot/bostie-assistants.webp){ align=right width="180" loading=lazy }

bostie スキルは、bostie の Bot の書き方をコーディングアシスタントに教えます。コマンド、コンポーネント、モーダル、イベント、REST、ボイス、テストを、完全なサンプルと、ほかのコードから持ち込むと bostie では失敗する癖とあわせて扱います。スキルに対応したアシスタントは、作業が bostie に関わるときにこのスキルを読み込みます。スキルはリポジトリの `docs/skills/bostie` にあり、bostie のテストはその中のすべてのサンプルを型チェックして実行し、記載されたすべての名前が実在することを確認します。

{% set expanded = true %}
{% include "callout.md" %}
{% from "links.md" import owner, name with context %}

## 自分でインストールする { #installing-it-yourself }

```sh
npx skills add {{ owner }}/{{ name }} --skill bostie --yes
```

を実行すると、現在のプロジェクトで見つかったコーディングアシスタント向けにスキルがインストールされます。`--global` を付けるとすべてのプロジェクト向けに、`--agent claude-code`、`--agent codex`、`--agent github-copilot` を付けるとそのアシスタントだけにインストールされます。`npx skills update` で更新できます。

### プラグインとして { #as-a-plugin }

一部のアシスタントでは、スキルをプラグインとしてすべてのプロジェクト向けにインストールし、ほかのプラグインと一緒に更新することもできます。

| アシスタント | インストール |
|---|---|
| Claude Code | `/plugin marketplace add {{ owner }}/{{ name }}` の後に `/plugin install bostie@bostie` |
| Codex CLI | `codex plugin marketplace add {{ owner }}/{{ name }}` の後に `codex plugin add bostie@bostie` |
| GitHub Copilot CLI | `copilot plugin marketplace add {{ owner }}/{{ name }}` の後に `copilot plugin install bostie@bostie` |
| VS Code with GitHub Copilot | コマンドパレットで **Chat: Install Plugin from Source** を実行し、`{{ owner }}/{{ name }}` を入力 |
| Devin | `devin plugins install {{ owner }}/{{ name }}#docs` |
| Factory Droid | `droid plugin marketplace add {{ owner }}/{{ name }}` の後に `droid plugin install bostie@bostie --scope user` |
| Qwen Code | `qwen extensions install {{ owner }}/{{ name }}:bostie` |

### 手動で { #by-hand }

リポジトリの `docs/skills/bostie` フォルダを、使っているアシスタントのプロジェクト用スキルフォルダにコピーします。

| アシスタント | フォルダ |
|---|---|
| Claude Code | `.claude/skills/bostie` |
| Codex、Cursor、GitHub Copilot、Gemini CLI、OpenCode | `.agents/skills/bostie` |
| Windsurf | `.windsurf/skills/bostie` |

## アシスタントにスキルを使わせる { #making-the-assistant-use-it }

スキルをいつ読み込むかはアシスタント自身が判断しますが、公開された評価では読み込まないことがよくありました。`AGENTS.md` のように、アシスタントが毎回のセッション開始時に読む指示ファイルに 1 行加えると、ほぼすべての実行でスキルが読み込まれました。そのため、プロンプトでは次の 1 行を追加します。

```text
Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.
```

スキルは bostie の main ブランチに追従しています。プロジェクトが古い bostie を使っている場合、スキルはアシスタントに、各名前をインストール済みのパッケージ（完全に型付けされています）と照合するよう指示します。
