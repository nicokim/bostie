---
source: 22e588eb286f2378e7b04e0a4e738f8828a23ecf
---

# Установка

![Бости открывает коробку со звездой внутри](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

Установите bostie в окружение своего бота:

```sh
pip install bostie
```

В wheel-пакетах ядро на Rust и libopus уже скомпилированы, поэтому pip ничего не компилирует и не требует других инструментов.

## Требования { #requirements }

bostie нужен Python 3.12 или новее. Wheel-пакеты есть для всех версий CPython начиная с 3.12:

- Linux на x86_64 и aarch64, с glibc 2.28 или новее либо с musl.
- macOS на Apple Silicon и Intel.
- Windows на x86_64.

Для сборок Python 3.14 и 3.15 без GIL (free-threaded) на каждой из этих платформ есть отдельные wheel-пакеты.

CI проверяет каждый коммит на Linux, macOS и Windows с Python 3.12, 3.13, 3.14 и 3.15.

## Дополнительная зависимость testing { #the-testing-extra }

`bostie.testing` — фейковый Discord, на котором работают ваши тесты, — требует ещё двух пакетов. Установите их через дополнительную зависимость `testing`:

```sh
pip install "bostie[testing]"
```

Для голоса больше ничего не нужно: декодирование, Opus и шифрование скомпилированы в bostie. Только источник [`voice.FFmpeg`](voice/playback.md#sources) запускает внешнюю программу, `ffmpeg`, и она должна быть в `PATH`.

## Сборка из исходников { #build-from-source }

Если подходящего wheel-пакета нет, pip собирает bostie из дистрибутива с исходным кодом. То же происходит при установке клона репозитория командой `pip install path/to/bostie`. Для сборки нужны:

- Rust 1.95 или новее, установленный через [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) и компилятор C — они собирают libopus.

Сборка компилирует ядро на Rust и libopus и занимает несколько минут.

## Проверка установки { #check-the-install }

```sh
python -m bostie
```

печатает версию bostie, реализацию и версию Python и платформу. Вставляйте эти три строки в отчёт об ошибке. В коде версия хранится в `bostie.__version__`, а `bostie.voice.versions()` возвращает версии bostie, libopus, библиотеки DAVE и протокола DAVE, скомпилированных в сборку.

## Дальше { #next }

В [быстром старте](quickstart.md) вы создадите приложение в Discord и запустите первого бота.
