---
source: 22e588eb286f2378e7b04e0a4e738f8828a23ecf
---

# Instalação

![Bostie abrindo uma caixa com uma estrela dentro](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

Instale o bostie no ambiente do seu bot:

```sh
pip install bostie
```

Os wheels já vêm com o núcleo em Rust e a libopus compilados, então o pip não compila nada e não precisa de nenhuma outra ferramenta.

## Requisitos { #requirements }

O bostie precisa do Python 3.12 ou mais recente. Os wheels cobrem todo CPython a partir do 3.12:

- Linux em x86_64 e aarch64, com glibc 2.28 ou mais recente, ou com musl.
- macOS em Apple Silicon e Intel.
- Windows em x86_64.

As builds free-threaded do Python 3.14 e do 3.15 têm wheels próprios em cada uma dessas plataformas.

A CI testa cada commit no Linux, no macOS e no Windows com Python 3.12, 3.13, 3.14 e 3.15.

## O extra testing { #the-testing-extra }

`bostie.testing`, o Discord falso contra o qual seus testes rodam, precisa de mais dois pacotes. Instale-os com o extra `testing`:

```sh
pip install "bostie[testing]"
```

A voz não precisa de mais nada: a decodificação, o Opus e a criptografia são compilados dentro do bostie. Só a fonte [`voice.FFmpeg`](voice/playback.md#sources) roda um programa externo, o `ffmpeg`, e ele precisa estar no `PATH`.

## Compilar a partir do código-fonte { #build-from-source }

Quando nenhum wheel serve, o pip compila o bostie a partir da distribuição de código-fonte dele. O mesmo acontece ao instalar um clone do repositório com `pip install path/to/bostie`. A compilação precisa de:

- Rust 1.95 ou mais recente, instalado com o [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) e um compilador C, que compilam a libopus.

Ela compila o núcleo em Rust e a libopus, o que leva alguns minutos.

## Verificar a instalação { #check-the-install }

```sh
python -m bostie
```

imprime a versão do bostie, a implementação e a versão do Python e a plataforma. Cole essas três linhas em um relatório de bug. `bostie.__version__` guarda a versão no código, e `bostie.voice.versions()` retorna as versões do bostie, da libopus, da biblioteca DAVE e do protocolo DAVE compiladas na build.

## Próximos passos { #next }

O [início rápido](quickstart.md) cria o aplicativo no Discord e roda um primeiro bot.
