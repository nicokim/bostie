---
source: 7849bf684b355c9ddd7819d46330ce19ea09b251
---

# Installation

![Bostie ouvre une boîte avec une étoile à l'intérieur](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

Vous compilez bostie à partir d'un clone de son dépôt. La compilation construit le cœur Rust et une libopus embarquée, ce qui prend quelques minutes la première fois.

## Prérequis { #requirements }

- Python 3.12 ou plus récent. Les builds free-threaded de 3.14 et 3.15 fonctionnent aussi.
- Rust 1.95 ou plus récent, installé avec [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) et un compilateur C, qui compilent libopus.

La CI teste chaque commit sous Linux, macOS et Windows avec Python 3.12, 3.13, 3.14 et 3.15.

## Installer depuis un clone { #install-from-a-clone }

Clonez le dépôt, puis installez le clone dans l'environnement de votre bot :

```sh
pip install path/to/bostie
```

`bostie.testing`, le Discord factice contre lequel vos tests s'exécutent, a besoin de deux paquets de plus. Installez-les avec l'extra `testing` :

```sh
pip install "path/to/bostie[testing]"
```

La voix n'a besoin de rien d'autre : le décodage, Opus et le chiffrement sont compilés dans bostie. Seule la source [`voice.FFmpeg`](voice/playback.md#sources) lance un programme externe, `ffmpeg`, qui doit se trouver dans le `PATH`.

## Vérifier l'installation { #check-the-install }

```sh
python -m bostie
```

affiche la version de bostie, l'implémentation et la version de Python, ainsi que la plateforme. Collez ces trois lignes dans un rapport de bug. `bostie.__version__` contient la version dans le code, et `bostie.voice.versions()` renvoie les versions de bostie, de libopus, de la bibliothèque DAVE et du protocole DAVE compilés dans le build.

## Et ensuite { #next }

Le [démarrage rapide](quickstart.md) crée l'application dans Discord et lance un premier bot.
