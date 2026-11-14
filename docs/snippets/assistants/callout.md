{% from "links.md" import repo, owner, name, site with context %}
!!! tip "Using an AI coding assistant?"

    Install the bostie skill to make your coding assistant better at bostie tasks. Copy the prompt and paste it into your coding assistant.{% if not expanded %} [More about the skill](assistants.md){% endif %}

    ???{{ "+" if expanded }} example "Install the bostie skill"

        ```text
        Install the bostie skill for my coding agent so it writes correct bostie code: Discord bots with slash commands, components, events, REST calls, voice and tests.

        Use the Agent Skills installer with the bostie repository, {{ repo }}:

            npx skills add {{ owner }}/{{ name }} --skill bostie --yes

        If I ask for a global install instead, use:

            npx skills add {{ owner }}/{{ name }} --skill bostie --yes --global

        Detect which agent or editor I am using. If I use Claude Code and prefer the plugin path, follow the plugin install on {{ site }}assistants/ (`/plugin marketplace add` then `/plugin install`). Do not invent other skill names or install URLs.

        Then add this line to the instructions file you read at the start of every session (AGENTS.md at the project root, or your global one for a global install), and create the file if needed:

            Before you write or change code that uses bostie, look at how the project is laid out, then load the bostie skill.

        After installing, confirm that the bostie skill is available to you.
        ```
