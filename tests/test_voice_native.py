import array
import asyncio
import os
import socket
import sys
import time
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

import pytest

from bostie import _native
from bostie._bridge import Bridge, GatewayDelivery

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
    application: Literal["audio", "voip", "lowdelay"] = "audio",
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


def opus_sink(core: _native.Core) -> _native.Sink:
    return core.voice_sink(
        GUILD,
        kind="opus",
        rate=48_000,
        channels=2,
        sample="s16le",
        buffer=10.0,
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


def encoder(
    *,
    rate: int = 48_000,
    channels: int = 2,
    sample: Literal["s16le", "f32le"] = "s16le",
    bitrate: int = 64_000,
) -> _native.OpusEncoder:
    return _native.OpusEncoder(
        rate=rate,
        channels=channels,
        sample=sample,
        bitrate=bitrate,
        application="audio",
        fec=True,
        packet_loss=10,
        complexity=10,
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


def test_a_sink_queues_until_its_buffer_is_full_and_clearing_resolves_its_marker(
    connected: _native.Core,
) -> None:
    sink = pcm_sink(connected)

    assert sink.write(bytes(FRAME_BYTES))
    assert sink.queued() == pytest.approx(0.02)
    marker, position = sink.mark()
    assert position == 960
    while sink.write(bytes(FRAME_BYTES * 5)):
        pass
    assert 0.2 <= sink.queued() <= 0.3
    handle_deliveries(connected, sink.id)
    sink.clear()

    assert sink.queued() == 0
    assert sorted(handle_deliveries(connected, sink.id)) == [
        ("voice_marker", sink.id, marker, False),
        ("voice_wake", sink.id),
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


def test_an_opus_sink_takes_encoded_packets_and_refuses_anything_else(
    connected: _native.Core,
) -> None:
    sink = opus_sink(connected)
    packet = encoder().encode(bytes(FRAME_BYTES))[0]

    assert sink.write(packet)
    assert sink.queued() == pytest.approx(0.02)
    with pytest.raises(ValueError, match="not a valid Opus packet"):
        sink.write(b"")


def test_a_closed_sink_refuses_writes_but_keeps_its_stats(
    connected: _native.Core,
) -> None:
    sink = pcm_sink(connected)
    sink.set_volume(0.5)

    sink.close()
    sink.close()

    with pytest.raises(RuntimeError, match=f"voice handle {sink.id} is closed"):
        sink.write(bytes(FRAME_BYTES))
    with pytest.raises(RuntimeError, match="closed"):
        sink.mark()
    stats = sink.stats()
    assert {"sent_samples", "consumed_samples", "underruns"} <= stats.keys()
    assert not sink.passthrough()


def test_sink_settings_are_checked(connected: _native.Core) -> None:
    sink = pcm_sink(connected)

    with pytest.raises(ValueError, match="volume"):
        sink.set_volume(-1.0)
    with pytest.raises(ValueError, match="20 ms blocks"):
        pcm_sink(connected, rate=44_101)
    with pytest.raises(ValueError, match="buffer"):
        pcm_sink(connected, buffer=0.0)
    with pytest.raises(ValueError, match="buffer"):
        pcm_sink(connected, buffer=-1.0)


def test_a_connection_reports_latency_dave_stats_and_diagnostics(
    connected: _native.Core,
) -> None:
    assert connected.voice_latency(GUILD) == (None, None)
    assert connected.voice_dave(GUILD) == (0, None, None)
    stats = connected.voice_stats(GUILD)
    assert stats["packets_sent"] == 0
    assert stats["recoveries"] == 0
    wait_until(lambda: bool(connected.voice_diagnostics(GUILD)["history"]))

    diagnostics = connected.voice_diagnostics(GUILD)

    assert diagnostics["versions"] == _native.versions()
    assert diagnostics["guild_id"] == GUILD
    assert diagnostics["channel_id"] == CHANNEL
    assert diagnostics["shard_id"] == 0
    assert diagnostics["status"] == "connecting"
    assert diagnostics["endpoint_host"] is None
    assert diagnostics["dave"] == {"protocol_version": 0, "epoch": None}
    assert diagnostics["members"] == []
    history = diagnostics["history"]
    assert isinstance(history, list)
    seconds_ago, status, reason, code, _ = history[0]
    assert 0 <= seconds_ago < 10
    assert (status, reason, code) == ("connecting", None, None)
    pacer = diagnostics["pacer"]
    assert isinstance(pacer, dict)
    assert pacer["lanes"] >= 1
    assert {
        "work_ewma_us",
        "work_max_us",
        "after_ewma_us",
        "after_p50_us",
        "after_p99_us",
        "after_max_us",
        "late_ticks",
    } <= pacer.keys()
    counters = diagnostics["counters"]
    assert isinstance(counters, dict)
    assert counters.keys() == stats.keys()


def test_a_receiver_on_a_connection_that_is_not_ready_waits_until_it_is_closed(
    connected: _native.Core,
) -> None:
    rx = receiver(connected)

    assert rx.read(256) == []
    assert rx.queued() == 0
    assert rx.closed_reason() is None
    assert connected.voice_diagnostics(GUILD)["receivers"] == 1
    rx.close()
    rx.close()

    assert rx.closed_reason() == "closed"
    assert handle_deliveries(connected, rx.id) == [("voice_wake", rx.id)]
    with pytest.raises(RuntimeError, match=f"voice handle {rx.id} is closed"):
        rx.read(256)
    assert rx.stats() == {"delivered": 0, "dropped": 0, "lost": 0}


def test_voice_errors_raise_runtime_or_value_errors(core: _native.Core) -> None:
    with pytest.raises(RuntimeError, match=f"no voice connection in guild {GUILD}"):
        core.voice_stats(GUILD)
    core.voice_disconnect(GUILD)
    with pytest.raises(RuntimeError, match="shard 0 is not running"):
        connect(core)
    with pytest.raises(ValueError, match="bitrate"):
        connect(core, bitrate=10)
    with pytest.raises(ValueError, match="speaking"):
        connect(core, speaking=0)
    with pytest.raises(ValueError, match="timeout"):
        connect(core, timeout=-1.0)
    with pytest.raises(ValueError, match="server_change_delay"):
        connect(core, server_change_delay=-1.0)
    core.gateway_start(0, "ws://127.0.0.1:1", b"{}")
    connect(core)
    with pytest.raises(ValueError, match="speech hangover must be from"):
        core.voice_speech_hangover(GUILD, 11.0)
    with pytest.raises(ValueError, match="buffer must be from"):
        receiver(core, buffer=0.0)
    with pytest.raises(ValueError, match="jitter must be at most"):
        receiver(core, jitter=2.0)
    with pytest.raises(ValueError, match="markers=True needs per-user frames"):
        receiver(core, markers=True, mix=True)
    with pytest.raises(RuntimeError, match="already has a voice connection"):
        connect(core)
    with pytest.raises(RuntimeError, match="code: there is no DAVE session"):
        core.voice_verification_code(GUILD, USER)

    core.close()

    with pytest.raises(RuntimeError, match="closed"):
        connect(core)
    with pytest.raises(RuntimeError, match="closed"):
        core.voice_file(str(MEDIA / "tone.flac"))


def test_a_file_source_yields_one_second_of_48_khz_stereo_f32(
    core: _native.Core,
) -> None:
    source = core.voice_file(str(MEDIA / "tone.flac"))

    kind, duration, seekable = probed(source)
    chunks = read_all(source)

    assert (kind, seekable) == ("pcm", True)
    assert duration == pytest.approx(1.0)
    assert sum(map(len, chunks)) == SECOND_OF_F32_STEREO
    assert source.error() is None
    assert source.read() is None


def test_an_opus_file_yields_one_packet_per_item(connected: _native.Core) -> None:
    source = connected.voice_file(str(MEDIA / "tone.opus"))
    sink = opus_sink(connected)

    assert probed(source)[0] == "opus"
    packets = read_all(source)

    assert len(packets) >= 50
    assert all(sink.write(packet) for packet in packets)
    assert sink.queued() == pytest.approx(1.0, abs=0.03)


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
    probed(source)

    source.seek(0.5)
    after_seek = sum(map(len, read_all(source)))
    with pytest.raises(ValueError, match="position"):
        source.seek(-1.0)
    source.close()

    assert after_seek == pytest.approx(SECOND_OF_F32_STEREO / 2, rel=0.01)
    with pytest.raises(RuntimeError, match=f"voice handle {source.id} is closed"):
        source.seek(0.0)
    with pytest.raises(RuntimeError, match="closed"):
        source.read()


def test_a_url_that_does_not_parse_is_a_value_error(core: _native.Core) -> None:
    with pytest.raises(ValueError):
        core.voice_url("not a url", [])


def test_the_opus_encoder_checks_its_settings() -> None:
    with pytest.raises(ValueError, match="bitrate"):
        encoder(bitrate=10)
    with pytest.raises(ValueError, match="20 ms blocks"):
        encoder(rate=44_101)
    with pytest.raises(ValueError, match="channels"):
        encoder(channels=3)


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
            lambda: connected.voice_stats(GUILD),
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


def test_the_bridge_hands_voice_deliveries_to_the_gateway_callback() -> None:
    async def main() -> None:
        handed: asyncio.Queue[GatewayDelivery] = asyncio.Queue()
        async with Bridge(
            "token",
            API_URL,
            lambda delivery, _: handed.put_nowait(delivery),
            global_limit=50,
        ) as bridge:
            source = bridge.core.voice_bytes(b"not audio", None)
            delivery = await asyncio.wait_for(handed.get(), 10)
            source.close()

        assert delivery == ("voice_wake", source.id)

    asyncio.run(main())
