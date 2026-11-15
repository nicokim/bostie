---
source: 7849bf684b355c9ddd7819d46330ce19ea09b251
---

# Instalación

![Bostie abre una caja con una estrella adentro](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

bostie se compila a partir de un clon de su repositorio. La compilación construye el núcleo en Rust y una libopus incluida, lo que tarda unos minutos la primera vez.

## Requisitos { #requirements }

- Python 3.12 o posterior. Las builds free-threaded de 3.14 y 3.15 también funcionan.
- Rust 1.95 o posterior, instalado con [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) y un compilador de C, que compilan libopus.

La CI prueba cada commit en Linux, macOS y Windows con Python 3.12, 3.13, 3.14 y 3.15.

## Instalar desde un clon { #install-from-a-clone }

Clona el repositorio y luego instala el clon en el entorno de tu bot:

```sh
pip install path/to/bostie
```

`bostie.testing`, el Discord falso contra el que corren tus pruebas, necesita dos paquetes más. Instálalos con el extra `testing`:

```sh
pip install "path/to/bostie[testing]"
```

La voz no necesita nada más: la decodificación, Opus y el cifrado vienen compilados en bostie. Solo la fuente [`voice.FFmpeg`](voice/playback.md#sources) ejecuta un programa externo, `ffmpeg`, y tiene que estar en el `PATH`.

## Comprobar la instalación { #check-the-install }

```sh
python -m bostie
```

imprime la versión de bostie, la implementación y la versión de Python, y la plataforma. Pega esas tres líneas en un reporte de error. `bostie.__version__` contiene la versión en el código, y `bostie.voice.versions()` devuelve las versiones de bostie, de libopus, de la biblioteca DAVE y del protocolo DAVE compiladas en la build.

## Siguiente paso { #next }

El [inicio rápido](quickstart.md) crea la aplicación en Discord y ejecuta un primer bot.
