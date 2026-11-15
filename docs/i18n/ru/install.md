---
source: 7849bf684b355c9ddd7819d46330ce19ea09b251
---

# Установка

![Бости открывает коробку со звездой внутри](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

bostie собирается из клона его репозитория. Сборка компилирует ядро на Rust и встроенную libopus, поэтому в первый раз она занимает несколько минут.

## Требования { #requirements }

- Python 3.12 или новее. Сборки 3.14 и 3.15 без GIL (free-threaded) тоже работают.
- Rust 1.95 или новее, установленный через [rustup](https://rustup.rs).
- [CMake](https://cmake.org/download/) и компилятор C — они собирают libopus.

CI проверяет каждый коммит на Linux, macOS и Windows с Python 3.12, 3.13, 3.14 и 3.15.

## Установка из клона { #install-from-a-clone }

Склонируйте репозиторий, затем установите клон в окружение своего бота:

```sh
pip install path/to/bostie
```

`bostie.testing` — фейковый Discord, на котором работают ваши тесты, — требует ещё двух пакетов. Установите их через дополнительную зависимость `testing`:

```sh
pip install "path/to/bostie[testing]"
```

Для голоса больше ничего не нужно: декодирование, Opus и шифрование скомпилированы в bostie. Только источник [`voice.FFmpeg`](voice/playback.md#sources) запускает внешнюю программу, `ffmpeg`, и она должна быть в `PATH`.

## Проверка установки { #check-the-install }

```sh
python -m bostie
```

печатает версию bostie, реализацию и версию Python и платформу. Вставляйте эти три строки в отчёт об ошибке. В коде версия хранится в `bostie.__version__`, а `bostie.voice.versions()` возвращает версии bostie, libopus, библиотеки DAVE и протокола DAVE, скомпилированных в сборку.

## Дальше { #next }

В [быстром старте](quickstart.md) вы создадите приложение в Discord и запустите первого бота.
