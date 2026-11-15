---
source: 7849bf684b355c9ddd7819d46330ce19ea09b251
---

# Installation

![Bostie öffnet eine Kiste mit einem Stern darin](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

Du baust bostie aus einem Klon seines Repositorys. Der Build kompiliert den Rust-Kern und ein mitgeliefertes libopus, was beim ersten Mal ein paar Minuten dauert.

## Voraussetzungen { #requirements }

- Python 3.12 oder neuer. Die Free-Threaded-Builds von 3.14 und 3.15 funktionieren ebenfalls.
- Rust 1.95 oder neuer, installiert mit [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) und ein C-Compiler, die libopus bauen.

Die CI testet jeden Commit unter Linux, macOS und Windows mit Python 3.12, 3.13, 3.14 und 3.15.

## Aus einem Klon installieren { #install-from-a-clone }

Klone das Repository und installiere den Klon dann in die Umgebung deines Bots:

```sh
pip install path/to/bostie
```

`bostie.testing`, das Fake-Discord, gegen das deine Tests laufen, braucht zwei weitere Pakete. Installiere sie mit dem Extra `testing`:

```sh
pip install "path/to/bostie[testing]"
```

Für Sprachkanäle brauchst du nichts weiter: Dekodierung, Opus und Verschlüsselung sind in bostie einkompiliert. Nur die Quelle [`voice.FFmpeg`](voice/playback.md#sources) startet ein externes Programm, `ffmpeg`, und das muss im `PATH` liegen.

## Die Installation prüfen { #check-the-install }

```sh
python -m bostie
```

gibt die Version von bostie, die Python-Implementierung samt Version und die Plattform aus. Füge diese drei Zeilen in einen Fehlerbericht ein. Im Code enthält `bostie.__version__` die Version, und `bostie.voice.versions()` gibt die Versionen von bostie, libopus, der DAVE-Bibliothek und des DAVE-Protokolls zurück, die in den Build einkompiliert sind.

## Weiter { #next }

Der [Schnellstart](quickstart.md) erstellt die Anwendung in Discord und startet einen ersten Bot.
