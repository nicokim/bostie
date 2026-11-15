---
source: 22e588eb286f2378e7b04e0a4e738f8828a23ecf
---

# Installation

![Bostie öffnet eine Kiste mit einem Stern darin](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

Installiere bostie in die Umgebung deines Bots:

```sh
pip install bostie
```

Die Wheels bringen den Rust-Kern und libopus fertig kompiliert mit, pip kompiliert also nichts und braucht kein weiteres Werkzeug.

## Voraussetzungen { #requirements }

bostie braucht Python 3.12 oder neuer. Wheels gibt es für jedes CPython ab 3.12:

- Linux auf x86_64 und aarch64, mit glibc 2.28 oder neuer oder mit musl.
- macOS auf Apple Silicon und Intel.
- Windows auf x86_64.

Die Free-Threaded-Builds von Python 3.14 und 3.15 haben auf jeder dieser Plattformen eigene Wheels.

Die CI testet jeden Commit unter Linux, macOS und Windows mit Python 3.12, 3.13, 3.14 und 3.15.

## Das Extra testing { #the-testing-extra }

`bostie.testing`, das Fake-Discord, gegen das deine Tests laufen, braucht zwei weitere Pakete. Installiere sie mit dem Extra `testing`:

```sh
pip install "bostie[testing]"
```

Für Sprachkanäle brauchst du nichts weiter: Dekodierung, Opus und Verschlüsselung sind in bostie einkompiliert. Nur die Quelle [`voice.FFmpeg`](voice/playback.md#sources) startet ein externes Programm, `ffmpeg`, und das muss im `PATH` liegen.

## Aus dem Quellcode bauen { #build-from-source }

Wo kein Wheel passt, baut pip bostie aus seiner Quelldistribution. Das gilt auch, wenn du einen Klon des Repositorys mit `pip install path/to/bostie` installierst. Der Build braucht:

- Rust 1.95 oder neuer, installiert mit [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) und einen C-Compiler, die libopus bauen.

Er kompiliert den Rust-Kern und libopus, was ein paar Minuten dauert.

## Die Installation prüfen { #check-the-install }

```sh
python -m bostie
```

gibt die Version von bostie, die Python-Implementierung samt Version und die Plattform aus. Füge diese drei Zeilen in einen Fehlerbericht ein. Im Code enthält `bostie.__version__` die Version, und `bostie.voice.versions()` gibt die Versionen von bostie, libopus, der DAVE-Bibliothek und des DAVE-Protokolls zurück, die in den Build einkompiliert sind.

## Weiter { #next }

Der [Schnellstart](quickstart.md) erstellt die Anwendung in Discord und startet einen ersten Bot.
