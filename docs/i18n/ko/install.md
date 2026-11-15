---
source: 22e588eb286f2378e7b04e0a4e738f8828a23ecf
---

# 설치

![별이 든 상자를 여는 Bostie](assets/mascot/bostie-install.webp){ align=right width="180" loading=lazy }

bostie를 봇의 환경에 설치합니다.

```sh
pip install bostie
```

wheel에는 Rust 코어와 libopus가 컴파일된 상태로 들어 있으므로, pip은 아무것도 컴파일하지 않고 다른 도구도 필요 없습니다.

## 요구 사항 { #requirements }

bostie에는 Python 3.12 이상이 필요합니다. wheel은 3.12 이상의 모든 CPython을 지원하며, 다음 환경용으로 제공됩니다.

- x86_64와 aarch64의 Linux(glibc 2.28 이상 또는 musl).
- Apple Silicon과 Intel의 macOS.
- x86_64의 Windows.

Python 3.14와 3.15의 free-threaded 빌드에도 위의 각 환경용 전용 wheel이 있습니다.

CI는 모든 커밋을 Linux, macOS, Windows에서 Python 3.12, 3.13, 3.14, 3.15로 테스트합니다.

## testing extra { #the-testing-extra }

테스트를 실행할 페이크 Discord인 `bostie.testing`에는 패키지가 두 개 더 필요합니다. `testing` extra로 함께 설치합니다.

```sh
pip install "bostie[testing]"
```

음성에는 따로 설치할 것이 없습니다. 디코딩, Opus, 암호화가 모두 bostie에 컴파일되어 있습니다. 외부 프로그램을 실행하는 것은 [`voice.FFmpeg`](voice/playback.md#sources) 소스뿐이며, 이 소스가 실행하는 `ffmpeg`는 `PATH`에 있어야 합니다.

## 소스에서 빌드하기 { #build-from-source }

맞는 wheel이 없으면 pip이 소스 배포판에서 bostie를 빌드합니다. 저장소 클론을 `pip install path/to/bostie`로 설치할 때도 마찬가지입니다. 빌드에는 다음이 필요합니다.

- [rustup](https://rustup.rs)으로 설치한 Rust 1.95 이상.
- libopus를 빌드할 [CMake](https://cmake.org/download/)와 C 컴파일러.

빌드 과정에서 Rust 코어와 libopus를 컴파일하므로 몇 분 정도 걸립니다.

## 설치 확인하기 { #check-the-install }

```sh
python -m bostie
```

위 명령은 bostie 버전, Python 구현체와 그 버전, 플랫폼을 출력합니다. 버그 리포트에는 이 세 줄을 붙여 넣으세요. 코드에서는 `bostie.__version__`에 버전이 들어 있고, `bostie.voice.versions()`는 빌드에 컴파일된 bostie, libopus, DAVE 라이브러리, DAVE 프로토콜의 버전을 반환합니다.

## 다음 단계 { #next }

[빠른 시작](quickstart.md)에서 Discord에 애플리케이션을 만들고 첫 봇을 실행합니다.
