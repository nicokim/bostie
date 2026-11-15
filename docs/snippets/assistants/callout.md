{% from "links.md" import repo, owner, name, site, t with context %}
!!! tip "{{ t("Using an AI coding assistant?") }}"

    {{ t("Install the bostie skill to make your coding assistant better at bostie tasks. Copy the prompt and paste it into your coding assistant.") }}{% if not expanded %} [{{ t("More about the skill") }}](assistants.md){% endif %}

    ???{{ "+" if expanded }} example "{{ t("Install the bostie skill") }}"

        ```text
        Install the bostie skill for my coding agent so it writes correct bostie code: Discord bots with slash commands, components, events, REST calls, voice and tests.

        Use the Agent Skills installer with the bostie repository, {{ repo }}:

            npx skills add {{ owner }}/{{ name }} --skill bostie --yes

        If I ask for a global install instead, use:

            npx skills add {{ owner }}/{{ name }} --skill bostie --yes --global

        Detect which agent or editor I am using. If it is one of these and I prefer the plugin path, install the bostie plugin instead, as {{ site }}assistants/ shows:

            Claude Code: /plugin marketplace add {{ owner }}/{{ name }}, then /plugin install bostie@bostie
            Codex CLI: codex plugin marketplace add {{ owner }}/{{ name }}, then codex plugin add bostie@bostie
            GitHub Copilot CLI: copilot plugin marketplace add {{ owner }}/{{ name }}, then copilot plugin install bostie@bostie
            VS Code with GitHub Copilot: run "Chat: Install Plugin from Source" and enter {{ owner }}/{{ name }}
            Devin: devin plugins install {{ owner }}/{{ name }}#docs
            Factory Droid: droid plugin marketplace add {{ owner }}/{{ name }}, then droid plugin install bostie@bostie --scope user
            Qwen Code: qwen extensions install {{ owner }}/{{ name }}:bostie

        Do not invent other skill names or install URLs.

        Then add this line to the instructions file you read at the start of every session (AGENTS.md at the project root, or your global one for a global install), and create the file if needed:

            Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.

        After installing, confirm that the bostie skill is available to you.
        ```
