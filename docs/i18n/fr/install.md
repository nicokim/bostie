---
source: 22e588eb286f2378e7b04e0a4e738f8828a23ecf
---

# Installation

![Bostie ouvre une boîte avec une étoile à l'intérieur](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

Installez bostie dans l'environnement de votre bot :

```sh
pip install bostie
```

Les wheels contiennent le cœur Rust et libopus déjà compilés : pip ne compile rien et n'a besoin d'aucun autre outil.

## Prérequis { #requirements }

bostie nécessite Python 3.12 ou plus récent. Les wheels couvrent chaque version de CPython à partir de 3.12 :

- Linux sur x86_64 et aarch64, avec glibc 2.28 ou plus récent, ou musl.
- macOS sur Apple Silicon et Intel.
- Windows sur x86_64.

Les builds free-threaded de Python 3.14 et 3.15 ont leurs propres wheels sur chacune de ces plateformes.

La CI teste chaque commit sous Linux, macOS et Windows avec Python 3.12, 3.13, 3.14 et 3.15.

## L'extra testing { #the-testing-extra }

`bostie.testing`, le Discord factice contre lequel vos tests s'exécutent, a besoin de deux paquets de plus. Installez-les avec l'extra `testing` :

```sh
pip install "bostie[testing]"
```

La voix n'a besoin de rien d'autre : le décodage, Opus et le chiffrement sont compilés dans bostie. Seule la source [`voice.FFmpeg`](voice/playback.md#sources) lance un programme externe, `ffmpeg`, qui doit se trouver dans le `PATH`.

## Compiler à partir des sources { #build-from-source }

Lorsqu'aucun wheel ne convient, pip compile bostie à partir de sa distribution source. C'est aussi le cas quand vous installez un clone du dépôt avec `pip install path/to/bostie`. La compilation nécessite :

- Rust 1.95 ou plus récent, installé avec [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) et un compilateur C, qui compilent libopus.

Elle compile le cœur Rust et libopus, ce qui prend quelques minutes.

## Vérifier l'installation { #check-the-install }

```sh
python -m bostie
```

affiche la version de bostie, l'implémentation et la version de Python, ainsi que la plateforme. Collez ces trois lignes dans un rapport de bug. `bostie.__version__` contient la version dans le code, et `bostie.voice.versions()` renvoie les versions de bostie, de libopus, de la bibliothèque DAVE et du protocole DAVE compilés dans le build.

## Et ensuite { #next }

Le [démarrage rapide](quickstart.md) crée l'application dans Discord et lance un premier bot.
