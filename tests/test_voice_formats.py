import ast
import math
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

import bostie
from bostie import voice
from bostie.enums import Direction, DisconnectReason, SpeakingFlags, State
from bostie.voice.events import LOCAL_NAMES, VoiceEvent

COMMON_RATES = [
    8_000,
    11_025,
    12_000,
    16_000,
    22_050,
    24_000,
    32_000,
    44_100,
    48_000,
    88_200,
    96_000,
    176_400,
    192_000,
]


def stub_literals(alias: str) -> list[str]:
    stub = ast.parse((Path(bostie.__file__).parent / "_native.pyi").read_text())
    for node in stub.body:
        if isinstance(node, ast.TypeAlias) and node.name.id == alias:
            return [
                literal.value
                for literal in ast.walk(node.value)
                if isinstance(literal, ast.Constant) and isinstance(literal.value, str)
            ]
    raise LookupError(alias)


@pytest.mark.parametrize("rate", COMMON_RATES)
def test_common_rates_are_accepted(rate: int) -> None:
    assert voice.PCM(rate, 1, "f32le").rate == rate


@pytest.mark.parametrize("rate", [8_001, 44_101])
def test_rates_whose_resampler_block_exceeds_a_frame_name_the_rule(rate: int) -> None:
    with pytest.raises(ValueError, match=r"gcd\(rate, 48000\) must be at most 960"):
        voice.PCM(rate)


@pytest.mark.parametrize("rate", [0, 7_999, 192_001])
def test_rates_outside_8_to_192_khz_are_rejected(rate: int) -> None:
    with pytest.raises(ValueError, match="from 8000 to 192000 Hz"):
        voice.PCM(rate)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("channels", 0, "channels"),
        ("channels", 6, "channels"),
        ("sample", "s24le", "sample"),
    ],
)
def test_pcm_rejects_unknown_layouts(field: str, value: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        voice.PCM(**{field: value})


def test_pcm_defaults_to_48_khz_stereo_s16le() -> None:
    assert voice.PCM() == voice.PCM(48_000, 2, "s16le")


@pytest.mark.parametrize(
    "settings",
    [
        {"bitrate": 500, "packet_loss": 0.0, "complexity": 0},
        {"bitrate": 512_000, "packet_loss": 1.0, "complexity": 10},
        {"application": "lowdelay", "fec": False},
    ],
)
def test_encoder_accepts_its_bounds(settings: dict[str, Any]) -> None:
    voice.Encoder(**settings)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("bitrate", 499, "bitrate"),
        ("bitrate", 512_001, "bitrate"),
        ("packet_loss", -0.01, "packet loss"),
        ("packet_loss", 10, "packet loss"),
        ("packet_loss", math.nan, "packet loss"),
        ("complexity", -1, "complexity"),
        ("complexity", 11, "complexity"),
        ("application", "music", "application"),
    ],
)
def test_encoder_rejects_values_past_its_bounds(
    field: str, value: Any, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        voice.Encoder(**{field: value})


def test_defaults_match_the_documented_values() -> None:
    defaults = voice.Defaults()

    assert defaults.encoder == voice.Encoder(
        bitrate=None, application="audio", fec=True, packet_loss=0.1, complexity=10
    )
    assert defaults.speaking_flags is SpeakingFlags.MICROPHONE
    assert (defaults.timeout, defaults.sink_buffer) == (10.0, 0.2)
    assert defaults.server_change_delay == 0.0
    assert (defaults.stage, defaults.stall) == ("listen", "hold")
    assert not defaults.self_deaf
    assert not defaults.self_mute


@pytest.mark.parametrize("sink_buffer", [0.02, 60.0])
def test_defaults_accept_buffers_from_20_ms_to_60_s(sink_buffer: float) -> None:
    assert voice.Defaults(sink_buffer=sink_buffer).sink_buffer == sink_buffer


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("timeout", 0.0, "timeout"),
        ("timeout", -1.0, "timeout"),
        ("timeout", math.inf, "timeout"),
        ("timeout", math.nan, "timeout"),
        ("server_change_delay", -0.1, "server_change_delay"),
        ("server_change_delay", math.inf, "server_change_delay"),
        ("server_change_delay", math.nan, "server_change_delay"),
        ("sink_buffer", 0.019, "sink_buffer"),
        ("sink_buffer", 60.1, "sink_buffer"),
        ("sink_buffer", math.nan, "sink_buffer"),
        ("speaking_flags", SpeakingFlags(0), "speaking_flags"),
        ("stage", "shout", "stage"),
        ("stall", "wait", "stall"),
    ],
)
def test_defaults_reject_values_past_their_bounds(
    field: str, value: Any, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        voice.Defaults(**{field: value})


@pytest.mark.parametrize(
    ("enum", "alias"),
    [
        (State, "VoiceStateName"),
        (DisconnectReason, "DisconnectReasonName"),
        (Direction, "VoiceStalledDelivery"),
    ],
)
def test_enum_values_match_the_native_names(enum: type[StrEnum], alias: str) -> None:
    native = [name for name in stub_literals(alias) if name != "voice_stalled"]

    assert [member.value for member in enum] == native


def test_voice_events_need_no_intent_and_have_local_names() -> None:
    events = VoiceEvent.__subclasses__()

    assert {event.NAME for event in events} == LOCAL_NAMES
    assert len(LOCAL_NAMES) == len(events)
    assert all(name.startswith("voice.") and name.islower() for name in LOCAL_NAMES)
    assert not VoiceEvent.INTENTS
    assert not any(event.INTENTS for event in events)


def test_speaking_flags_keep_unknown_bits() -> None:
    flags = SpeakingFlags(SpeakingFlags.PRIORITY | 1 << 9)

    assert SpeakingFlags.PRIORITY in flags
    assert flags == 1 << 9 | 4


def test_voice_connect_error_names_the_reason_code_and_step() -> None:
    error = voice.VoiceConnectError(
        DisconnectReason.REMOVED, 4014, "kicked from the channel"
    )
    bare = voice.VoiceConnectError(DisconnectReason.TIMEOUT, None, "")

    assert str(error) == (
        "voice connection failed: removed (close code 4014): kicked from the channel"
    )
    assert str(bare) == "voice connection failed: timeout"
    assert (error.reason, error.code) == (DisconnectReason.REMOVED, 4014)
    assert isinstance(error, bostie.BostieError)


def test_source_error_names_the_source() -> None:
    error = voice.SourceError("File('a.mp3')", "unknown format")

    assert str(error) == "File('a.mp3'): unknown format"
    assert isinstance(error, voice.VoiceError)
