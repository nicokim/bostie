import socket
from collections.abc import Callable, Iterator

import pytest

from bostie import (
    BadRequest,
    GatewayQueueFull,
    JSONErrorCode,
    ShardNotRunning,
    StateError,
    _native,
    voice,
)

API_URL = "http://127.0.0.1:1/api/v10"
GATEWAY_URL = "ws://127.0.0.1:1"
PRESENCE = b'{"op":3,"d":null}'
GUILD = 1_335_172_792_320_131_073
CHANNEL = 1_335_173_295_636_611_074
USER = 1_329_027_298_099_331_073

type Call = Callable[[_native.Core], object]


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
        core.gateway_start(0, GATEWAY_URL, b"{}")
        connect(core)
        yield core
        core.close()


def connect(core: _native.Core) -> None:
    core.voice_connect(
        GUILD,
        CHANNEL,
        shard_id=0,
        user_id=USER,
        self_mute=False,
        self_deaf=False,
        timeout=60.0,
        server_change_delay=0.0,
        bitrate=64_000,
        application="audio",
        fec=True,
        packet_loss=10,
        complexity=10,
        speaking=1,
        bandwidth="auto",
        signal="auto",
    )


def use_closed_core(core: _native.Core) -> None:
    core.close()
    core.request("GET", "/users/@me", {}, [])


def fill_send_queue(core: _native.Core) -> None:
    for _ in range(1_000):
        core.gateway_send(0, PRESENCE)


def use_closed_sink(core: _native.Core) -> None:
    sink = core.voice_sink(
        GUILD,
        kind="opus",
        rate=48_000,
        channels=2,
        sample="s16le",
        buffer=1.0,
        volume=1.0,
        stall="hold",
    )
    sink.close()
    sink.flush()


def use_closed_receiver(core: _native.Core) -> None:
    receiver = core.voice_receive(
        GUILD,
        kind="opus",
        rate=48_000,
        channels=2,
        sample="s16le",
        users=None,
        exclude=[],
        include_self=False,
        buffer=1.0,
        jitter=0.1,
        markers=False,
        mix=False,
        overflow="drop_oldest",
        stall="wait",
    )
    receiver.close()
    receiver.read(1)


def use_closed_source(core: _native.Core) -> None:
    source = core.voice_bytes(b"", None)
    source.close()
    source.read()


@pytest.mark.parametrize(
    ("call", "error"),
    [
        pytest.param(
            lambda core: core.request("BREW", "/users/@me", {}, []),
            ValueError,
            id="InvalidRequest",
        ),
        pytest.param(use_closed_core, StateError, id="Closed"),
        pytest.param(
            lambda core: core.gateway_send(1, PRESENCE),
            ShardNotRunning,
            id="UnknownShard",
        ),
        pytest.param(
            lambda core: core.gateway_start(0, GATEWAY_URL, b"{}"),
            StateError,
            id="ShardRunning",
        ),
        pytest.param(fill_send_queue, GatewayQueueFull, id="SendQueueFull"),
        pytest.param(
            lambda core: core.voice_dave(GUILD + 1),
            voice.VoiceNotConnected,
            id="UnknownVoice",
        ),
        pytest.param(connect, voice.AlreadyConnected, id="VoiceRunning"),
        pytest.param(use_closed_sink, voice.SinkClosed, id="SinkClosed"),
        pytest.param(use_closed_receiver, voice.ReceiverClosed, id="ReceiverClosed"),
        pytest.param(use_closed_source, StateError, id="SourceClosed"),
        pytest.param(
            lambda core: core.voice_verification_code(GUILD, USER),
            voice.DaveError,
            id="Dave",
        ),
    ],
)
def test_each_native_error_raises_its_bostie_class(
    core: _native.Core, call: Call, error: type[Exception]
) -> None:
    with pytest.raises(error) as raised:
        call(core)

    assert type(raised.value) is error


def test_an_http_error_lists_each_form_error_under_its_field_path() -> None:
    errors = {
        "activities": {
            "0": {
                "platform": {
                    "_errors": [{"code": "BASE_TYPE_CHOICES", "message": "Bad."}]
                },
                "type": {"_errors": [{"code": "BASE_TYPE_CHOICES", "message": "No."}]},
            }
        },
        "_errors": [{"code": "APPLICATION_COMMAND_TOO_LARGE", "message": "Too big."}],
    }

    error = BadRequest(400, 50035, "Invalid Form Body", errors, b"")

    assert error.code is JSONErrorCode.INVALID_FORM_BODY_OR_CONTENT_TYPE
    assert str(error).splitlines() == [
        "400 Invalid Form Body (error code 50035)",
        "activities.0.platform: Bad.",
        "activities.0.type: No.",
        "Too big.",
    ]
