import math
from contextlib import AbstractContextManager, nullcontext
from enum import StrEnum
from typing import Any, get_args

import pytest

import bostie
from bostie import _native, voice
from bostie.enums import ClosedReason, Direction, DisconnectReason, SpeakingFlags, State
from bostie.voice.events import LOCAL_NAMES, VoiceEvent

VALID = nullcontext()


def invalid(message: str) -> AbstractContextManager[object]:
    return pytest.raises(ValueError, match=message)


@pytest.mark.parametrize(
    ("settings", "outcome"),
    [
        ({"rate": 8_000, "channels": 1, "sample": "f32le"}, VALID),
        ({"rate": 192_000, "channels": 2}, VALID),
        ({"rate": 7_999}, invalid("from 8000 to 192000 Hz")),
        ({"rate": 192_001}, invalid("from 8000 to 192000 Hz")),
        ({"rate": 44_101}, invalid(r"gcd\(rate, 48000\) must be at most 960")),
        ({"channels": 0}, invalid("channels")),
        ({"channels": 3}, invalid("channels")),
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
        ({"bitrate": 499}, invalid("bitrate")),
        ({"bitrate": 512_001}, invalid("bitrate")),
        ({"packet_loss": -0.01}, invalid("packet loss")),
        ({"packet_loss": 1.01}, invalid("packet loss")),
        ({"packet_loss": math.nan}, invalid("packet loss")),
        ({"complexity": -1}, invalid("complexity")),
        ({"complexity": 11}, invalid("complexity")),
        ({"application": "music"}, invalid("application")),
        ({"bandwidth": "full", "signal_type": "music"}, VALID),
        ({"bandwidth": "ultra"}, invalid("bandwidth")),
        ({"signal_type": "noise"}, invalid("signal_type")),
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
        (
            {
                "sink_buffer": 0.02,
                "speech_hangover": 0.02,
                "stall_after": 0.02,
                "receive_buffer": 0.02,
                "jitter": 0.0,
                "stage": "request",
                "stall": "drop",
            },
            VALID,
        ),
        (
            {
                "sink_buffer": 60.0,
                "speech_hangover": 10.0,
                "receive_buffer": 60.0,
                "jitter": 1.0,
                "stage": "speak",
                "overflow": "drop_newest",
                "receive_stall": "end",
                "crossfade": 60.0,
                "history": 100,
            },
            VALID,
        ),
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
        ({"stall_after": 0.019}, invalid("stall_after")),
        ({"stall_after": math.inf}, invalid("stall_after")),
        ({"stall_after": math.nan}, invalid("stall_after")),
        ({"receive_buffer": 0.019}, invalid("receive_buffer")),
        ({"receive_buffer": 60.1}, invalid("receive_buffer")),
        ({"receive_buffer": math.nan}, invalid("receive_buffer")),
        ({"jitter": -0.01}, invalid("jitter")),
        ({"jitter": 1.01}, invalid("jitter")),
        ({"jitter": math.nan}, invalid("jitter")),
        ({"overflow": "drop_all"}, invalid("overflow")),
        ({"receive_stall": "hold"}, invalid("receive_stall")),
        ({"crossfade": -0.1}, invalid("crossfade")),
        ({"crossfade": 60.1}, invalid("crossfade")),
        ({"crossfade": math.nan}, invalid("crossfade")),
        ({"history": -1}, invalid("history")),
    ],
)
def test_defaults_check_their_bounds(
    settings: dict[str, Any], outcome: AbstractContextManager[object]
) -> None:
    with outcome:
        voice.Defaults(**settings)


@pytest.mark.parametrize(
    ("settings", "outcome"),
    [
        ({"equalizer": {20: -24.0, 20_000: 24.0}, "speed": 0.5, "pitch": 2.0}, VALID),
        ({"equalizer": {19.9: 1.0}}, invalid("equalizer frequencies")),
        ({"equalizer": {100: 24.1}}, invalid("equalizer gains")),
        (
            {"equalizer": {float(hz): 1.0 for hz in range(100, 1_800, 100)}},
            invalid("16"),
        ),
        ({"bass": math.nan}, invalid("bass")),
        ({"speed": 2.1}, invalid("speed")),
        ({"pitch": 0.49}, invalid("pitch")),
    ],
)
def test_filters_check_their_bounds(
    settings: dict[str, Any], outcome: AbstractContextManager[object]
) -> None:
    with outcome:
        voice.Filters(**settings)


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
    ("enum", "names"),
    [
        (State, _native.VOICE_STATES),
        (DisconnectReason, _native.DISCONNECT_REASONS),
        (Direction, _native.DIRECTIONS),
        (ClosedReason, _native.CLOSED_REASONS),
    ],
)
def test_enum_values_are_the_names_rust_sends(
    enum: type[StrEnum], names: list[str]
) -> None:
    assert [member.value for member in enum] == names


@pytest.mark.parametrize(
    ("literal", "names"),
    [
        (voice.OpusApplication.__value__, _native.OPUS_APPLICATIONS),
        (voice.OpusBandwidth.__value__, _native.OPUS_BANDWIDTHS),
        (voice.OpusSignal.__value__, _native.OPUS_SIGNALS),
        (voice.Sample.__value__, _native.SAMPLES),
        (voice.SinkStall.__value__, _native.SINK_STALLS),
        (voice.ReceiveStall.__value__, _native.RECEIVE_STALLS),
        (voice.Overflow.__value__, _native.OVERFLOWS),
    ],
)
def test_option_literals_are_the_names_rust_accepts(
    literal: object, names: list[str]
) -> None:
    assert list(get_args(literal)) == names


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
