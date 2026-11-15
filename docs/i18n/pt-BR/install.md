---
source: 7849bf684b355c9ddd7819d46330ce19ea09b251
---

# Instalação

![Bostie abrindo uma caixa com uma estrela dentro](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

Você compila o bostie a partir de um clone do repositório dele. A compilação compila o núcleo em Rust e uma libopus embutida, o que leva alguns minutos na primeira vez.

## Requisitos { #requirements }

- Python 3.12 ou mais recente. As builds free-threaded do 3.14 e do 3.15 também funcionam.
- Rust 1.95 ou mais recente, instalado com o [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) e um compilador C, que compilam a libopus.

A CI testa cada commit no Linux, no macOS e no Windows com Python 3.12, 3.13, 3.14 e 3.15.

## Instalar a partir de um clone { #install-from-a-clone }

Clone o repositório e depois instale o clone no ambiente do seu bot:

```sh
pip install path/to/bostie
```

`bostie.testing`, o Discord falso contra o qual seus testes rodam, precisa de mais dois pacotes. Instale-os com o extra `testing`:

```sh
pip install "path/to/bostie[testing]"
```

A voz não precisa de mais nada: a decodificação, o Opus e a criptografia são compilados dentro do bostie. Só a fonte [`voice.FFmpeg`](voice/playback.md#sources) roda um programa externo, o `ffmpeg`, e ele precisa estar no `PATH`.

## Verificar a instalação { #check-the-install }

```sh
python -m bostie
```

imprime a versão do bostie, a implementação e a versão do Python e a plataforma. Cole essas três linhas em um relatório de bug. `bostie.__version__` guarda a versão no código, e `bostie.voice.versions()` retorna as versões do bostie, da libopus, da biblioteca DAVE e do protocolo DAVE compiladas na build.

## Próximos passos { #next }

O [início rápido](quickstart.md) cria o aplicativo no Discord e roda um primeiro bot.
