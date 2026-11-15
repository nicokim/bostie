{% set repo = (config.repo_url or "https://github.com/OWNER/REPO").rstrip("/") %}
{% set owner, name = repo.split("/")[-2:] %}
{% set site = config.site_url or "https://OWNER.github.io/REPO/" %}
{% macro t(text) %}{{ (config.extra.strings or {}).get(text, text) }}{% endmacro %}
