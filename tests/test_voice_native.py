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
from bostie.errors import ReceiverClosed, ShardNotRunning, SourceError, StateError
from bostie.voice.formats import OpusApplication

MEDIA = Path(__file__).parents[1] / "crates/bostie-core/src/voice/testdata/media"
API_URL = "http://127.0.0.1:1/api/v10"
GUILD = 1_335_172_792_320_131_073
CHANNEL = 1_335_173_295_636_611_074
USER = 1_329_027_298_099_331_073
FRAME_BYTES = 960 * 2 * 2
SECOND_OF_F32_STEREO = 48_000 * 2 * 4


@pytest.fixture
def core() -> Iterator[_native.Core]:
    core, bell = _native.Core.create("token", API_URL, 50)
    with socket.socket(fileno=bell):
        yield core
        core.close()


@pytest.fixture
def connected(core: _native.Core) -> _native.Core:
    core.gateway_start(0, "ws://127.0.0.1:1", b"{}")
    connect(core)
    return core


def connect(
    core: _native.Core,
    *,
    timeout: float = 60.0,
    server_change_delay: float = 0.0,
    bitrate: int = 64_000,
    application: OpusApplication = "audio",
    speaking: int = 1,
) -> None:
    core.voice_connect(
        GUILD,
        CHANNEL,
        shard_id=0,
        user_id=USER,
        self_mute=False,
        self_deaf=False,
        timeout=timeout,
        server_change_delay=server_change_delay,
        bitrate=bitrate,
        application=application,
        fec=True,
        packet_loss=10,
        complexity=10,
        speaking=speaking,
    )


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


def handle_deliveries(core: _native.Core, handle: int) -> "list[_native.Delivery]":
    return [
        delivery
        for delivery in core.drain()
        if delivery[0] in ("voice_wake", "voice_marker") and delivery[1] == handle
    ]


def test_flush_pads_the_last_frame_and_marks_the_end(connected: _native.Core) -> None:
    sink = pcm_sink(connected)
    sink.write(bytes(400))

    marker, position = sink.flush()

    assert position == 960
    assert sink.queued() == pytest.approx(0.02)
    assert sink.mark() == (marker + 1, 960)


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


def test_a_receiver_on_a_connection_not_yet_ready_waits_until_closed(
    connected: _native.Core,
) -> None:
    rx = receiver(connected)

    assert rx.read(256) == []
    assert rx.queued() == 0
    assert rx.closed_reason() is None
    rx.close()
    rx.close()

    assert rx.closed_reason() == "closed"
    assert handle_deliveries(connected, rx.id) == [("voice_wake", rx.id)]
    with pytest.raises(ReceiverClosed, match=f"receiver {rx.id} is closed"):
        rx.read(256)
    assert rx.stats() == (0, 0, 0)


def test_voice_connect_needs_a_running_shard_and_valid_settings(
    core: _native.Core,
) -> None:
    core.voice_disconnect(GUILD)
    with pytest.raises(ShardNotRunning, match="shard 0 is not running"):
        connect(core)
    with pytest.raises(ValueError, match="bitrate"):
        connect(core, bitrate=10)
    with pytest.raises(ValueError, match="timeout"):
        connect(core, timeout=-1.0)
    with pytest.raises(ValueError, match="server_change_delay"):
        connect(core, server_change_delay=-1.0)
    core.gateway_start(0, "ws://127.0.0.1:1", b"{}")
    connect(core)
    with pytest.raises(ValueError, match="buffer"):
        pcm_sink(core, buffer=-1.0)
    with pytest.raises(ValueError, match="mix=True needs a PCM format"):
        core.voice_receive(
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


def test_a_bytes_source_probes_with_its_hint(core: _native.Core) -> None:
    source = core.voice_bytes((MEDIA / "tone.mp3").read_bytes(), "mp3")

    assert probed(source)[0] == "pcm"
    assert sum(map(len, read_all(source))) == pytest.approx(
        SECOND_OF_F32_STEREO, rel=0.05
    )


def test_undecodable_bytes_end_with_an_error(core: _native.Core) -> None:
    source = core.voice_bytes(b"not audio", None)

    wait_until(lambda: source.error() is not None)

    assert source.info() is None
    assert source.read() is None
    assert str(source.error()).startswith("no decoder")
    with pytest.raises(ValueError, match="not seekable"):
        source.seek(0.0)


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


@pytest.mark.skipif(sys.platform != "linux", reason="fork is only reliable on Linux")
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
