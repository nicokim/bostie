import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
I18N = DOCS / "i18n"
STAGE = ".i18n"
CONFIG = ROOT / "zensical.toml"
OUTDATED = (
    "The English page has changed since this page was translated. "
    "Read the English page."
)
SOURCE = re.compile(r"^source: *(\S+)", re.MULTILINE)
MACRO_STRING = re.compile(r'\bt\("([^"]+)"\)')


def project() -> dict[str, Any]:
    config: dict[str, Any] = tomllib.loads(CONFIG.read_text("utf-8"))["project"]
    return config


LANGUAGES: dict[str, str] = project()["extra"]["languages"]


def pages(nav: list[Any]) -> list[str]:
    found: list[str] = []
    for item in nav:
        for value in item.values() if isinstance(item, dict) else [item]:
            found += pages(value) if isinstance(value, list) else [value]
    return found


def labels(nav: list[Any]) -> list[str]:
    found: list[str] = []
    for item in nav:
        for label, value in item.items() if isinstance(item, dict) else []:
            found += [label, *(labels(value) if isinstance(value, list) else [])]
    return found


def translated(nav: list[Any], strings: dict[str, str]) -> list[Any]:
    return [
        {
            strings.get(label, label): (
                translated(value, strings) if isinstance(value, list) else value
            )
            for label, value in item.items()
        }
        if isinstance(item, dict)
        else item
        for item in nav
    ]


def english_strings() -> list[str]:
    config = project()
    toggles = [entry["toggle"]["name"] for entry in config["theme"]["palette"]]
    macros = [
        text
        for path in sorted((DOCS / "snippets" / "assistants").glob("*.md"))
        for text in MACRO_STRING.findall(path.read_text("utf-8"))
    ]
    description = config["site_description"]
    return [description, *labels(config["nav"]), *toggles, *macros, OUTDATED]


def strings(code: str) -> dict[str, str]:
    path = I18N / code / "strings.toml"
    return tomllib.loads(path.read_text("utf-8")) if path.is_file() else {}


def source(page: str) -> str:
    data = (DOCS / page).read_bytes().replace(b"\r\n", b"\n")
    blob = b"blob %d\0" % len(data) + data
    return hashlib.sha1(blob, usedforsecurity=False).hexdigest()


def split(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    head, _, body = text.partition("\n---\n")
    return f"{head}\n---\n", body


def recorded(text: str) -> str | None:
    found = SOURCE.search(split(text)[0])
    return found[1] if found else None


def missing(code: str) -> list[str]:
    return [
        page for page in pages(project()["nav"]) if not (I18N / code / page).is_file()
    ]


def stale(code: str) -> list[tuple[str, str | None, str]]:
    found: list[tuple[str, str | None, str]] = []
    for page in pages(project()["nav"]):
        path = I18N / code / page
        old = recorded(path.read_text("utf-8")) if path.is_file() else "missing"
        if old != (new := source(page)):
            found.append((page, old, new))
    return found


def english_link(page: str) -> str:
    path = PurePosixPath(page).with_suffix("")
    url = f"{path.parent if path.name == 'index' else path}/".removeprefix("./")
    return "../" * (page.count("/") + 1) + url


def stage(code: str) -> None:
    into = ROOT / STAGE / code
    shutil.rmtree(into, ignore_errors=True)
    shutil.copytree(DOCS / "assets", into / "assets")
    notice = strings(code).get(OUTDATED, OUTDATED)
    for page in pages(project()["nav"]):
        text = (I18N / code / page).read_text("utf-8")
        if recorded(text) != source(page):
            head, body = split(text)
            text = f'{head}!!! info ""\n    [{notice}]({english_link(page)})\n\n{body}'
        (into / page).parent.mkdir(parents=True, exist_ok=True)
        (into / page).write_text(text, "utf-8")


def language_config(code: str | None) -> dict[str, Any]:
    config = project()
    site_url = config["site_url"]
    up = "" if code is None else "../"
    alternate = [
        {"name": "English", "lang": "en", "link": up or "./", "prefix": ""}
    ] + [
        {
            "name": name,
            "lang": other,
            "link": f"{up}{other.lower()}/",
            "prefix": f"{other.lower()}/",
        }
        for other, name in LANGUAGES.items()
    ]
    config["extra"] |= {
        "alternate": alternate if LANGUAGES else [],
        "scope": urlsplit(site_url).path,
        "root": site_url,
    }
    if code is None:
        return config
    words = strings(code)
    config |= {
        "docs_dir": f"{STAGE}/{code}",
        "site_dir": f"site/{code.lower()}",
        "site_url": f"{site_url}{code.lower()}/",
        "site_description": words.get(
            config["site_description"], config["site_description"]
        ),
        "nav": translated(config["nav"], words),
    }
    config["theme"]["language"] = code
    config["plugins"]["llmstxt"] = {"enabled": False}
    for entry in config["theme"]["palette"]:
        entry["toggle"]["name"] = words.get(
            entry["toggle"]["name"], entry["toggle"]["name"]
        )
    config["extra"]["strings"] = words
    return config


def robots() -> str:
    site_url = project()["site_url"]
    sitemaps = [f"{site_url}sitemap.xml"] + [
        f"{site_url}{code.lower()}/sitemap.xml" for code in LANGUAGES
    ]
    return "User-agent: *\nAllow: /\n\n" + "".join(
        f"Sitemap: {url}\n" for url in sitemaps
    )


def toml(value: object) -> str:
    if isinstance(value, dict):
        items = (f"{toml(key)} = {toml(item)}" for key, item in value.items())
        return "{" + ", ".join(items) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(map(toml, value)) + "]"
    return json.dumps(value, ensure_ascii=False)


def build(code: str | None) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=ROOT,
        prefix=".zensical-",
        suffix=".toml",
        delete_on_close=False,
    ) as file:
        file.write(f"project = {toml(language_config(code))}\n")
        file.close()
        command = [sys.executable, "-m", "zensical", "build", "--strict"]
        result = subprocess.run([*command, "-f", file.name], cwd=ROOT, check=False)
    if result.returncode:
        sys.exit(f"the {code or 'English'} build failed")


def main(args: list[str]) -> None:
    match args:
        case ["build"]:
            shutil.rmtree(ROOT / "site", ignore_errors=True)
            build(None)
            for code in LANGUAGES:
                if gaps := missing(code):
                    sys.exit(f"{code} has no translation of {', '.join(gaps)}")
                stage(code)
                build(code)
            (ROOT / "site" / "robots.txt").write_text(robots(), "utf-8")
        case ["stale", *codes]:
            for code in codes or LANGUAGES:
                for page, old, new in stale(code):
                    sys.stdout.write(f"{code}/{page} {old or 'untranslated'} {new}\n")
                words = strings(code)
                for text in english_strings():
                    if text not in words:
                        sys.stdout.write(f"{code}/strings.toml {toml(text)}\n")
        case _:
            sys.exit("usage: languages.py build | stale [code ...]")


if __name__ == "__main__":
    main(sys.argv[1:])
