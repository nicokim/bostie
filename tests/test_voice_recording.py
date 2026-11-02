import asyncio
import time
import wave
from collections import deque
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Self, cast

import pytest
from fakediscord import settle

from bostie.enums import ClosedReason
from bostie.errors import Empty, ReceiverClosed
from bostie.voice.events import SpeechStart, SpeechStop, UserLeave
from bostie.voice.formats import PCM, Opus, ReceiveFormat
from bostie.voice.frames import Frame
from bostie.voice.layers import Recorder, Utterance, utterances

SETTLE = 20

STEP = 960
SPEECH_PCM = PCM(8_000, 2, "f32le")
SPEECH_BYTES = 1280
SILENT = b"\xf8\xff\xfe"
OGG_FIXTURE = bytes.fromhex(
    "4f676753000200000000000000002a000000000000004fd5ab2601134f70757348656164"
    "0102000080bb00000000004f676753000000000000000000002a000000010000003236988a"
    "01164f7075735461677306000000626f73746965000000004f6767530004c0030000000000"
    "002a00000002000000f4e8daaf0103fcfffe"
)

type Received = Frame | SpeechStart | SpeechStop | UserLeave


class FakeReceiver:
    def __init__(
        self,
        format: ReceiveFormat = SPEECH_PCM,
        *,
        markers: bool = True,
        mix: bool = False,
        jitter: float = 0.1,
    ) -> None:
        self.format = format
        self.markers = markers
        self.mix = mix
        self.jitter = jitter
        self.items: deque[Received] = deque()
        self.buffered: list[Received] = []
        self.marks: list[asyncio.Future[bool]] = []
        self.closed_reason: ClosedReason | None = None
        self.arrival: asyncio.Future[None] | None = None

    def feed(self, *items: Received) -> None:
        self.items.extend(items)
        self.wake()

    def hear(self, *items: Received) -> None:
        self.buffered.extend(items)

    def release(self) -> None:
        self.feed(*self.buffered)
        self.buffered.clear()
        self.settle_marks(True)

    def mark(self) -> asyncio.Future[bool]:
        future = asyncio.get_running_loop().create_future()
        self.marks.append(future)
        if self.closed_reason is not None:
            self.settle_marks(False)
        elif not self.buffered:
            self.settle_marks(True)
        return future

    def settle_marks(self, passed: bool) -> None:
        for future in self.marks:
            future.set_result(passed)
        self.marks.clear()

    def end(self, reason: ClosedReason) -> None:
        self.closed_reason = self.closed_reason or reason
        self.settle_marks(False)
        self.wake()

    def close(self) -> None:
        self.items.clear()
        self.end(ClosedReason.CLOSED)

    def get_nowait(self) -> Received:
        if self.items:
            return self.items.popleft()
        if self.closed_reason is not None:
            raise ReceiverClosed("the receiver ended and has nothing left to read")
        raise Empty("nothing is queued in the receiver")

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Received:
        while not self.items:
            if self.closed_reason is not None:
                raise StopAsyncIteration
            self.arrival = asyncio.get_running_loop().create_future()
            await self.arrival
        return self.items.popleft()

    def wake(self) -> None:
        if self.arrival is not None and not self.arrival.done():
            self.arrival.set_result(None)


class RecordingConnection:
    guild_id = 7

    def __init__(self) -> None:
        self.rx = FakeReceiver(Opus(), markers=False)
        self.opened: list[tuple[ReceiveFormat, dict[str, Any]]] = []

    def receive(self, format: ReceiveFormat, **options: Any) -> FakeReceiver:
        self.opened.append((format, options))
        return self.rx


def run(scenario: Callable[[], Coroutine[Any, Any, None]]) -> None:
    async def main() -> None:
        failures: list[dict[str, Any]] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: failures.append(context)
        )
        async with asyncio.timeout(10):
            await scenario()
        assert failures == []

    asyncio.run(main())


def frame(
    user: int, index: int, *, lost: bool = False, level: float | None = None
) -> Frame:
    data = bytes([user * 16 + index]) * SPEECH_BYTES
    return Frame(user, user, index * STEP, index * STEP, STEP, 0.0, data, lost, level)


def start(user: int, index: int) -> SpeechStart:
    return SpeechStart(guild_id=7, user_id=user, ssrc=user, at=0.0, offset=index * STEP)


def stop(user: int, index: int, *, at: float = 0.0) -> SpeechStop:
    return SpeechStop(
        guild_id=7, user_id=user, ssrc=user, at=at, duration=0.0, offset=index * STEP
    )


def leave(user: int, index: int) -> UserLeave:
    return UserLeave(guild_id=7, user_id=user, offset=index * STEP)


def heard(user: int, *indexes: int | None, lost: int = 0) -> Utterance:
    data = b"".join(
        bytes(SPEECH_BYTES) if index is None else frame(user, index).data
        for index in indexes
    )
    first = next(index for index in indexes if index is not None)
    return Utterance(
        user, data, first * STEP, len(indexes) * STEP / 48_000, lost * STEP / 48_000
    )


def pcm(user: int | None, index: int, data: bytes) -> Frame:
    return Frame(user, user, index * STEP, index * STEP, STEP, 0.0, data, False)


def opus(index: int, data: bytes | None = None, *, lost: bool = False) -> Frame:
    packet = bytes([index]) * 3 if data is None else data
    return Frame(42, 42, index * STEP, index * STEP, STEP, 0.0, packet, lost)


def read_ogg(path: Path) -> tuple[list[bytes], list[int]]:
    data = path.read_bytes()
    packets: list[bytes] = []
    granules: list[int] = []
    partial = b""
    while data:
        count = data[26]
        page = bytearray(data[: 27 + count + sum(data[27 : 27 + count])])
        page[22:26] = bytes(4)
        assert ogg_crc(page) == int.from_bytes(data[22:26], "little")
        body = data[27 + count :]
        granules.append(int.from_bytes(data[6:14], "little"))
        for size in data[27 : 27 + count]:
            partial += body[:size]
            body = body[size:]
            if size < 255:
                packets.append(partial)
                partial = b""
        data = body
    return packets[2:], granules


def ogg_crc(page: bytearray) -> int:
    crc = 0
    for byte in page:
        crc ^= byte << 24
        for _ in range(8):
            crc = (crc << 1) ^ 0x104C11DB7 if crc & 0x80000000 else crc << 1
    return crc


@pytest.mark.parametrize(
    "rx",
    [
        FakeReceiver(Opus()),
        FakeReceiver(markers=False),
        FakeReceiver(mix=True),
    ],
    ids=["opus", "no-markers", "mix"],
)
def test_utterances_refuses_a_receiver_it_cannot_group_at_the_call(
    rx: FakeReceiver,
) -> None:
    with pytest.raises(ValueError, match="markers=True"):
        utterances(cast("Any", rx))


@pytest.mark.parametrize(
    ("items", "max_length", "expected"),
    [
        pytest.param(
            [
                *(start(1, 1), frame(1, 1), frame(1, 2), stop(1, 2)),
                *(start(1, 6), frame(1, 6)),
            ],
            30.0,
            [heard(1, 1, 2, None, None, None, 6)],
            id="a-shorter-pause-is-filled-with-zeros",
        ),
        pytest.param(
            [
                *(start(1, 1), frame(1, 1), frame(1, 2), stop(1, 2)),
                *(start(1, 7), frame(1, 7)),
            ],
            30.0,
            [heard(1, 1, 2), heard(1, 7)],
            id="min-silence-counts-from-the-stop",
        ),
        pytest.param(
            [
                *(start(1, 1), frame(1, 1), stop(1, 2), leave(1, 2)),
                *(start(1, 3), frame(1, 3)),
            ],
            30.0,
            [heard(1, 1), heard(1, 3)],
            id="a-leave-ends-it-at-once",
        ),
        pytest.param(
            [start(1, 1), *(frame(1, index) for index in range(1, 6))],
            0.06,
            [heard(1, 1, 2, 3), heard(1, 4, 5)],
            id="max-length-cuts",
        ),
        pytest.param(
            [frame(1, 1), frame(1, 2), frame(1, 8)],
            30.0,
            [heard(1, 1, 2), heard(1, 8)],
            id="a-gap-without-a-stop",
        ),
        pytest.param(
            [start(1, 1), frame(1, 1), start(1, 2), frame(1, 2)],
            30.0,
            [heard(1, 1), heard(1, 2)],
            id="a-second-start",
        ),
        pytest.param(
            [start(1, 1), frame(1, 1), frame(1, 2, lost=True), frame(1, 3, lost=True)],
            30.0,
            [heard(1, 1, 2, 3, lost=2)],
            id="lost-seconds",
        ),
        pytest.param(
            [
                *(start(1, 1), start(2, 1), frame(1, 1), frame(2, 1)),
                *(stop(1, 2), frame(2, 2)),
            ],
            30.0,
            [heard(1, 1), heard(2, 1, 2)],
            id="one-per-user",
        ),
    ],
)
def test_utterances_split_speech_at_stops_gaps_starts_and_max_length(
    items: list[Received], max_length: float, expected: list[Utterance]
) -> None:
    async def scenario() -> None:
        rx = FakeReceiver()
        rx.feed(*items)
        rx.end(ClosedReason.DISCONNECTED)
        grouped = utterances(cast("Any", rx), min_silence=0.1, max_length=max_length)
        assert [utterance async for utterance in grouped] == expected

    run(scenario)


def test_utterances_with_a_level_follow_loudness_instead_of_markers() -> None:
    async def scenario() -> None:
        rx = FakeReceiver(markers=False)
        loud = [frame(1, index, level=-20.0) for index in (1, 2, 8)]
        quiet = [frame(1, index, level=-60.0) for index in (0, 3, 4, 5, 6, 7)]
        rx.feed(quiet[0], *loud[:2], start(1, 3), *quiet[1:], loud[2], stop(1, 9))
        rx.end(ClosedReason.DISCONNECTED)
        grouped = utterances(cast("Any", rx), min_silence=0.1, level=-40.0)
        assert [utterance async for utterance in grouped] == [
            heard(1, 1, 2),
            heard(1, 8),
        ]

    run(scenario)


def test_an_utterance_ends_jitter_and_min_silence_after_its_stop() -> None:
    async def scenario() -> None:
        rx = FakeReceiver(jitter=10.0)
        grouped: list[Utterance] = []

        async def listen() -> None:
            async for utterance in utterances(cast("Any", rx), min_silence=5.0):
                grouped.append(utterance)

        listening = asyncio.create_task(listen())
        rx.feed(start(1, 1), frame(1, 1), stop(1, 2, at=time.monotonic() - 10.0))
        await settle(SETTLE)
        assert grouped == []
        rx.feed(start(1, 4), frame(1, 4), stop(1, 5, at=time.monotonic() - 20.0))
        await settle(SETTLE)
        assert grouped == [heard(1, 1, None, None, 4)]
        rx.end(ClosedReason.DISCONNECTED)
        await listening
        assert len(grouped) == 1

    run(scenario)


def test_a_recorder_writes_ogg_opus_pages_into_its_path_template(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        vc = RecordingConnection()
        template = tmp_path / "{guild_id}" / "{user_id}.opus"
        async with Recorder(cast("Any", vc), template) as recorder:
            vc.rx.feed(Frame(42, 42, 0, 0, STEP, 0.0, b"\xfc\xff\xfe", False))
        path = tmp_path / "7" / "42.opus"
        assert recorder.files == {42: path}
        assert path.read_bytes() == OGG_FIXTURE
        assert vc.opened == [
            (
                Opus(),
                {
                    "users": None,
                    "exclude": (),
                    "include_self": False,
                    "buffer": 30.0,
                    "jitter": 0.3,
                    "stall": "wait",
                },
            )
        ]

    run(scenario)


@pytest.mark.parametrize(
    ("align", "expected"),
    [
        (
            True,
            (
                [
                    *(SILENT, SILENT, b"\x02" * 3, b"\x03" * 3, SILENT, b"\x05" * 3),
                    *(SILENT, b"\x07" * 3, *[SILENT] * 102, b"n" * 3),
                ],
                [0, 0, 48_000, 96_000, 106_560],
            ),
        ),
        (
            False,
            (
                [b"\x02" * 3, b"\x03" * 3, b"\x05" * 3, b"\x07" * 3, b"n" * 3],
                [0, 0, 4_800],
            ),
        ),
    ],
    ids=["aligned", "speech-only"],
)
def test_a_recorder_pads_the_start_and_gaps_with_silence_only_when_aligned(
    tmp_path: Path, align: bool, expected: tuple[list[bytes], list[int]]
) -> None:
    async def scenario() -> None:
        vc = RecordingConnection()
        recorder = Recorder(cast("Any", vc), tmp_path / "{user_id}.ogg", align=align)
        vc.rx.feed(opus(2), opus(3), opus(4, b"", lost=True), opus(5))
        vc.rx.feed(opus(6, bytes(65_025)), opus(7), opus(110))
        await recorder.stop()
        assert read_ogg(tmp_path / "42.ogg") == expected

    run(scenario)


@pytest.mark.parametrize(
    ("path", "mix"),
    [
        ("rec/{user_id}.mp3", False),
        ("rec/everyone.ogg", False),
        ("rec/{channel_id}/{user_id}.ogg", False),
        ("rec/{}{user_id}.ogg", False),
        ("rec/{user_id}.wav", True),
    ],
)
def test_a_recorder_refuses_a_path_that_does_not_fit_its_files(
    path: str, mix: bool
) -> None:
    vc = RecordingConnection()
    with pytest.raises(ValueError, match="path"):
        Recorder(cast("Any", vc), path, mix=mix)
    assert vc.opened == []


def test_a_wav_recorder_writes_pcm_and_pads_the_gaps_with_silence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        vc = RecordingConnection()
        format = PCM(8_000, 1)
        async with Recorder(cast("Any", vc), tmp_path / "{user_id}.wav", format=format):
            vc.rx.feed(pcm(42, 1, b"\x01\x00" * 160), pcm(42, 3, b"\x02\x00" * 160))
        with wave.open(str(tmp_path / "42.wav")) as wav:
            params = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
            data = wav.readframes(wav.getnframes())
        assert params == (1, 2, 8_000)
        silence = bytes(320)
        assert data == silence + b"\x01\x00" * 160 + silence + b"\x02\x00" * 160
        assert vc.opened[0][0] == format
        assert vc.opened[0][1]["mix"] is False
        with pytest.raises(ValueError, match="s16le"):
            Recorder(cast("Any", vc), "{user_id}.wav", format=PCM(sample="f32le"))

    run(scenario)


@pytest.mark.parametrize("suffix", [".ogg", ".wav"])
def test_a_mixed_recorder_writes_the_whole_call_to_one_file(
    tmp_path: Path, suffix: str
) -> None:
    async def scenario() -> None:
        vc = RecordingConnection()
        path = tmp_path / f"{{guild_id}}{suffix}"
        async with Recorder(cast("Any", vc), path, mix=True) as recorder:
            vc.rx.feed(*(pcm(None, index, bytes(3_840)) for index in range(3)))
        assert recorder.files == {None: tmp_path / f"7{suffix}"}
        assert vc.opened[0][1]["mix"] is True
        if suffix == ".ogg":
            assert len(read_ogg(tmp_path / "7.ogg")[0]) == 3
        else:
            with wave.open(str(tmp_path / "7.wav")) as wav:
                assert (wav.getnchannels(), wav.getnframes()) == (2, 3 * STEP)

    run(scenario)


def test_stop_waits_for_the_audio_still_in_the_jitter_buffer(tmp_path: Path) -> None:
    async def scenario() -> None:
        vc = RecordingConnection()
        recorder = Recorder(cast("Any", vc), tmp_path / "{user_id}.ogg", align=False)
        await settle(SETTLE)
        vc.rx.feed(*(opus(index) for index in range(5)))
        vc.rx.hear(opus(5))
        stopping = asyncio.create_task(recorder.stop())
        await settle(SETTLE)
        waited = not stopping.done()
        vc.rx.release()
        await stopping
        packets, _ = read_ogg(tmp_path / "42.ogg")
        assert waited
        assert packets == [bytes([index]) * 3 for index in range(6)]

    run(scenario)


def test_stop_finishes_the_files_when_waiting_for_the_audio_gives_up(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        vc = RecordingConnection()
        recorder = Recorder(cast("Any", vc), tmp_path / "{user_id}.ogg", align=False)
        await settle(SETTLE)
        vc.rx.feed(opus(0))
        vc.rx.hear(opus(1))
        stopping = asyncio.create_task(recorder.stop())
        await settle(SETTLE)
        vc.rx.settle_marks(False)
        files = await stopping
        assert read_ogg(files[42])[0] == [b"\x00" * 3]
        assert vc.rx.closed_reason is ClosedReason.CLOSED

    run(scenario)


def test_a_recorder_raises_its_write_error_from_stop(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "taken").write_bytes(b"")
        vc = RecordingConnection()
        recorder = Recorder(cast("Any", vc), tmp_path / "taken" / "{user_id}.ogg")
        vc.rx.feed(opus(0))
        with pytest.raises(OSError, match="taken"):
            await recorder.stop()
        assert vc.rx.closed_reason is ClosedReason.CLOSED

    run(scenario)


def test_a_recorder_finishes_its_files_when_its_receiver_moved_away(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        vc = RecordingConnection()
        recorder = Recorder(cast("Any", vc), tmp_path / "{user_id}.ogg", align=False)
        await settle(SETTLE)
        vc.rx.feed(opus(0), opus(1))
        vc.rx.end(ClosedReason.MOVED)
        await settle(SETTLE)
        files = await recorder.stop()
        assert files == {42: tmp_path / "42.ogg"}
        assert read_ogg(files[42]) == ([b"\x00" * 3, b"\x01" * 3], [0, 0, 1_920])

    run(scenario)


def test_a_recorder_ends_on_its_own_at_max_duration(tmp_path: Path) -> None:
    async def scenario() -> None:
        vc = RecordingConnection()
        path = tmp_path / "{user_id}.ogg"
        recorder = Recorder(cast("Any", vc), path, align=False, max_duration=0.06)
        vc.rx.feed(*(opus(index) for index in range(5)))
        files = await recorder.wait()
        assert read_ogg(files[42])[0] == [bytes([index]) * 3 for index in range(3)]
        assert vc.rx.closed_reason is ClosedReason.CLOSED
        with pytest.raises(ValueError, match="max_duration"):
            Recorder(cast("Any", vc), path, max_duration=0.0)

    run(scenario)


def test_a_recorder_ends_on_its_own_once_its_files_reach_max_size(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        vc = RecordingConnection()
        path = tmp_path / "{user_id}.wav"
        format = PCM(8_000, 1)
        recorder = Recorder(cast("Any", vc), path, format=format, max_size=700)
        vc.rx.feed(*(pcm(42, index, bytes([index]) * 320) for index in range(5)))
        files = await recorder.wait()
        with wave.open(str(files[42])) as wav:
            assert wav.readframes(wav.getnframes()) == b"".join(
                bytes([i]) * 320 for i in range(3)
            )
        with pytest.raises(ValueError, match="max_size"):
            Recorder(cast("Any", vc), path, max_size=0)

    run(scenario)
