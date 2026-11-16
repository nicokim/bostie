---
source: 2ee95f317499fe403f0b728d5caa99b8854b93d5
description: "Instala bostie con pip: wheels para Linux, macOS y Windows en Python 3.12 o superior, builds free-threaded, el extra testing y builds desde el código fuente."
---

# Instalación

![Bostie abre una caja con una estrella adentro](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

Instala bostie en el entorno de tu bot:

```sh
pip install bostie
```

Los wheels traen el núcleo en Rust y libopus ya compilados, así que pip no compila nada y no necesita ninguna otra herramienta.

## Requisitos { #requirements }

bostie necesita Python 3.12 o posterior. Los wheels cubren todas las versiones de CPython a partir de la 3.12:

- Linux en x86_64 y aarch64, con glibc 2.28 o posterior, o con musl.
- macOS en Apple Silicon e Intel.
- Windows en x86_64.

Las builds free-threaded de Python 3.14 y 3.15 tienen sus propios wheels en cada una de estas plataformas.

La CI prueba cada commit en Linux, macOS y Windows con Python 3.12, 3.13, 3.14 y 3.15.

## El extra testing { #the-testing-extra }

`bostie.testing`, el Discord falso contra el que corren tus pruebas, necesita dos paquetes más. Instálalos con el extra `testing`:

```sh
pip install "bostie[testing]"
```

La voz no necesita nada más: la decodificación, Opus y el cifrado vienen compilados en bostie. Solo la fuente [`voice.FFmpeg`](voice/playback.md#sources) ejecuta un programa externo, `ffmpeg`, y tiene que estar en el `PATH`.

## Compilar desde el código fuente { #build-from-source }

Cuando ningún wheel sirve, pip compila bostie a partir de su distribución de código fuente. Lo mismo pasa al instalar un clon del repositorio con `pip install path/to/bostie`. La compilación necesita:

- Rust 1.95 o posterior, instalado con [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) y un compilador de C, que compilan libopus.

Compila el núcleo en Rust y libopus, lo que tarda unos minutos.

## Comprobar la instalación { #check-the-install }

```sh
python -m bostie
```

imprime la versión de bostie, la implementación y la versión de Python, y la plataforma. Pega esas tres líneas en un reporte de error. `bostie.__version__` contiene la versión en el código, y `bostie.voice.versions()` devuelve las versiones de bostie, de libopus, de la biblioteca DAVE y del protocolo DAVE compiladas en la build.

## Siguiente paso { #next }

El [inicio rápido](quickstart.md) crea la aplicación en Discord y ejecuta un primer bot.
