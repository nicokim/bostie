import ast
import math
from contextlib import AbstractContextManager, nullcontext
from enum import StrEnum
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest

import bostie
from bostie import voice
from bostie.enums import ClosedReason, Direction, DisconnectReason, SpeakingFlags, State
from bostie.voice.events import LOCAL_NAMES, VoiceEvent

VALID = nullcontext()


def invalid(message: str) -> AbstractContextManager[object]:
    return pytest.raises(ValueError, match=message)


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


@pytest.mark.parametrize(
    ("settings", "outcome"),
    [
        ({"rate": 8_000, "channels": 1, "sample": "f32le"}, VALID),
        ({"rate": 11_025}, VALID),
        ({"rate": 192_000}, VALID),
        ({"rate": 8_001}, invalid(r"gcd\(rate, 48000\) must be at most 960")),
        ({"rate": 44_101}, invalid(r"gcd\(rate, 48000\) must be at most 960")),
        ({"rate": 7_999}, invalid("from 8000 to 192000 Hz")),
        ({"rate": 192_001}, invalid("from 8000 to 192000 Hz")),
        ({"channels": 0}, invalid("channels")),
        ({"channels": 6}, invalid("channels")),
        ({"sample": "s24le"}, invalid("sample")),
    ],
)
def test_pcm_checks_its_rate_channels_and_sample(
    settings: dict[str, Any], outcome: AbstractContextManager[object]
) -> None:
    with outcome:
        voice.PCM(**settings)


@pytest.mark.parametrize(
    ("settings", "outcome"),
    [
        ({"bitrate": 500, "packet_loss": 0.0, "complexity": 0}, VALID),
        ({"bitrate": 512_000, "packet_loss": 1.0, "complexity": 10}, VALID),
        ({"application": "lowdelay", "fec": False}, VALID),
        ({"bitrate": 499}, invalid("bitrate")),
        ({"bitrate": 512_001}, invalid("bitrate")),
        ({"packet_loss": -0.01}, invalid("packet loss")),
        ({"packet_loss": 10}, invalid("packet loss")),
        ({"packet_loss": math.nan}, invalid("packet loss")),
        ({"complexity": -1}, invalid("complexity")),
        ({"complexity": 11}, invalid("complexity")),
        ({"application": "music"}, invalid("application")),
    ],
)
def test_encoder_checks_its_bounds(
    settings: dict[str, Any], outcome: AbstractContextManager[object]
) -> None:
    with outcome:
        voice.EncoderSettings(**settings)


@pytest.mark.parametrize(
    ("settings", "outcome"),
    [
        ({"sink_buffer": 0.02}, VALID),
        ({"sink_buffer": 60.0}, VALID),
        ({"speech_hangover": 0.02, "receive_buffer": 0.02, "jitter": 0.0}, VALID),
        ({"speech_hangover": 10.0, "receive_buffer": 60.0, "jitter": 1.0}, VALID),
        ({"timeout": 0.0}, invalid("timeout")),
        ({"timeout": math.inf}, invalid("timeout")),
        ({"timeout": math.nan}, invalid("timeout")),
        ({"server_change_delay": -0.1}, invalid("server_change_delay")),
        ({"server_change_delay": math.inf}, invalid("server_change_delay")),
        ({"server_change_delay": math.nan}, invalid("server_change_delay")),
        ({"sink_buffer": 0.019}, invalid("sink_buffer")),
        ({"sink_buffer": 60.1}, invalid("sink_buffer")),
        ({"sink_buffer": math.nan}, invalid("sink_buffer")),
        ({"speaking_flags": SpeakingFlags(0)}, invalid("speaking_flags")),
        ({"stage": "shout"}, invalid("stage")),
        ({"stall": "wait"}, invalid("stall")),
        ({"speech_hangover": 0.019}, invalid("speech_hangover")),
        ({"speech_hangover": 10.1}, invalid("speech_hangover")),
        ({"speech_hangover": math.nan}, invalid("speech_hangover")),
        ({"receive_buffer": 0.019}, invalid("receive_buffer")),
        ({"receive_buffer": 60.1}, invalid("receive_buffer")),
        ({"receive_buffer": math.nan}, invalid("receive_buffer")),
        ({"jitter": -0.01}, invalid("jitter")),
        ({"jitter": 1.01}, invalid("jitter")),
        ({"jitter": math.nan}, invalid("jitter")),
        ({"overflow": "drop_newest"}, invalid("overflow")),
        ({"receive_stall": "hold"}, invalid("receive_stall")),
    ],
)
def test_defaults_check_their_bounds(
    settings: dict[str, Any], outcome: AbstractContextManager[object]
) -> None:
    with outcome:
        voice.Defaults(**settings)


def test_formats_default_to_the_documented_values() -> None:
    defaults = voice.Defaults()

    assert voice.PCM() == voice.PCM(48_000, 2, "s16le")
    assert defaults.encoder == voice.EncoderSettings(
        bitrate=None, application="audio", fec=True, packet_loss=0.1, complexity=10
    )
    assert defaults.speaking_flags is SpeakingFlags.MICROPHONE
    assert (defaults.timeout, defaults.sink_buffer) == (10.0, 0.2)
    assert defaults.server_change_delay == 0.0
    assert (defaults.stage, defaults.stall) == ("listen", "hold")
    assert (defaults.speech_hangover, defaults.receive_buffer) == (0.2, 2.0)
    assert defaults.jitter == 0.1
    assert (defaults.overflow, defaults.receive_stall) == ("drop_oldest", "wait")
    assert not defaults.self_deaf
    assert not defaults.self_mute


@pytest.mark.parametrize(
    ("enum", "alias"),
    [
        (State, "VoiceStateName"),
        (DisconnectReason, "DisconnectReasonName"),
        (Direction, "VoiceStalledDelivery"),
        (ClosedReason, "ClosedReasonName"),
    ],
)
def test_enum_values_match_the_native_names(enum: type[StrEnum], alias: str) -> None:
    native = [name for name in stub_literals(alias) if name != "voice_stalled"]

    assert [member.value for member in enum] == native


@pytest.mark.parametrize(
    ("field", "alias"),
    [("overflow", "OverflowName"), ("receive_stall", "ReceiveStallName")],
)
def test_receive_defaults_take_the_native_names(field: str, alias: str) -> None:
    assert list(get_args(get_type_hints(voice.Defaults)[field])) == stub_literals(alias)


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
