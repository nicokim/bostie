import array
import asyncio
import os
import socket
import sys
import time
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from bostie import _native, voice
from bostie.errors import SourceError, StateError

MEDIA = Path(__file__).parents[1] / "crates/bostie-core/src/voice/testdata/media"
API_URL = "http://127.0.0.1:1/api/v10"
GUILD = 1_335_172_792_320_131_073
CHANNEL = 1_335_173_295_636_611_074
USER = 1_329_027_298_099_331_073
FRAME_BYTES = 960 * 2 * 2
SECOND_OF_F32_STEREO = 48_000 * 2 * 4


@pytest.fixture
def core() -> Iterator[_native.Core]:
    core, bell = _native.Core.create(
        "token",
        API_URL,
        global_limit=50,
        max_rate_limit_wait=30.0,
        retries=2,
        connect_timeout=10.0,
        read_timeout=60.0,
        rate_limiter=True,
        proxy=None,
    )
    with socket.socket(fileno=bell):
        yield core
        core.close()


@pytest.fixture
def connected(core: _native.Core) -> _native.Core:
    core.gateway_start(0, "ws://127.0.0.1:1", b"{}")
    core.voice_connect(
        GUILD,
        CHANNEL,
        shard_id=0,
        user_id=USER,
        self_mute=False,
        self_deaf=False,
        timeout=60.0,
        server_change_delay=0.0,
        encoder=voice.EncoderSettings(bitrate=64_000),
        speaking=1,
    )
    return core


def pcm_sink(
    core: _native.Core, *, rate: int = 48_000, buffer: float = 0.2
) -> _native.Sink:
    return core.voice_sink(
        GUILD,
        kind="pcm",
        rate=rate,
        channels=2,
        sample="s16le",
        buffer=buffer,
        volume=1.0,
        stall="hold",
    )


def receiver(
    core: _native.Core,
    *,
    buffer: float = 2.0,
    jitter: float = 0.1,
    markers: bool = False,
    mix: bool = False,
) -> _native.Receiver:
    return core.voice_receive(
        GUILD,
        kind="pcm",
        rate=48_000,
        channels=2,
        sample="s16le",
        users=None,
        exclude=[],
        include_self=False,
        buffer=buffer,
        jitter=jitter,
        markers=markers,
        mix=mix,
        overflow="drop_oldest",
        stall="wait",
    )


def wait_until(done: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not done():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.005)


def probed(source: _native.Source) -> tuple[str, float | None, bool]:
    wait_until(lambda: source.info() is not None or source.error() is not None)
    info = source.info()
    assert info is not None, source.error()
    return info


def read_all(source: _native.Source) -> list[bytes]:
    chunks: list[bytes] = []
    deadline = time.monotonic() + 10
    while (read := source.read()) is not None:
        chunks.extend(read)
        if not read:
            assert time.monotonic() < deadline, "the source never ended"
            time.sleep(0.005)
    return chunks


def test_sinks_take_bytes_bytearrays_and_memoryview_slices(
    connected: _native.Core,
) -> None:
    sink = pcm_sink(connected)
    samples = array.array("h", [0] * 960)

    sink.write(bytes(FRAME_BYTES))
    sink.write(bytearray(FRAME_BYTES))
    sink.write(memoryview(bytes(FRAME_BYTES * 2))[FRAME_BYTES:])
    sink.write(memoryview(samples).cast("B"))

    assert sink.queued() == pytest.approx(0.07)


def test_a_connection_reports_latency_and_dave(connected: _native.Core) -> None:
    assert connected.voice_latency(GUILD) == (None, None)
    assert connected.voice_dave(GUILD) == (0, None, None)


def test_invalid_sink_and_receive_settings_raise_value_error(
    connected: _native.Core,
) -> None:
    with pytest.raises(ValueError, match="buffer"):
        pcm_sink(connected, buffer=-1.0)
    with pytest.raises(ValueError, match="mix=True needs a PCM format"):
        connected.voice_receive(
            GUILD,
            kind="opus",
            rate=48_000,
            channels=2,
            sample="s16le",
            users=None,
            exclude=[],
            include_self=False,
            buffer=2.0,
            jitter=0.1,
            markers=False,
            mix=True,
            overflow="drop_oldest",
            stall="wait",
        )


def test_seeking_restarts_the_stream_until_the_source_closes(
    core: _native.Core,
) -> None:
    source = core.voice_file(str(MEDIA / "tone.flac"))
    kind, duration, seekable = probed(source)
    assert (kind, duration, seekable) == ("pcm", 1.0, True)

    source.seek(0.5)
    after_seek = sum(map(len, read_all(source)))
    with pytest.raises(ValueError, match="position"):
        source.seek(-1.0)
    source.close()

    assert after_seek == pytest.approx(SECOND_OF_F32_STEREO / 2, rel=0.01)
    with pytest.raises(StateError, match=f"source {source.id} is closed"):
        source.seek(0.0)
    with pytest.raises(StateError, match="closed"):
        source.read()


def test_voice_message_measures_the_audio_and_names_the_file() -> None:
    async def main() -> None:
        file = await voice.message(MEDIA / "tone.opus")
        assert (file.name, file.data) == (
            "tone.opus",
            (MEDIA / "tone.opus").read_bytes(),
        )
        assert file.duration_secs == pytest.approx(1.0, abs=0.01)
        assert file.waveform is not None
        assert len(file.waveform) == 11
        with pytest.raises(SourceError, match=r"clip\.ogg"):
            await voice.message(b"not audio", name="clip.ogg")

    asyncio.run(main())


@pytest.mark.skipif(sys.platform != "linux", reason="fork is only reliable on Linux")
def test_voice_handles_refuse_to_work_in_a_forked_child(
    connected: _native.Core,
) -> None:
    sink = pcm_sink(connected)
    source = connected.voice_file(str(MEDIA / "tone.flac"))
    rx = receiver(connected)
    read_end, write_end = os.pipe()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        refused = []
        for call in (
            lambda: sink.write(bytes(FRAME_BYTES)),
            source.read,
            lambda: rx.read(256),
            lambda: connected.voice_dave(GUILD),
        ):
            try:
                call()
            except RuntimeError as error:
                refused.append("forked child" in str(error))
        os.write(write_end, b"refused" if refused == [True] * 4 else b"worked")
        os._exit(0)

    os.close(write_end)
    os.waitpid(pid, 0)
    assert os.read(read_end, 64) == b"refused"
    os.close(read_end)
    assert sink.write(bytes(FRAME_BYTES))
