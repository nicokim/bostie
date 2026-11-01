import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Literal, assert_type

import pytest
from voice_fakes import (
    GUILD_ID,
    NO_BITRATE,
    OTHER_GUILD_ID,
    OTHER_VOICE,
    USER_ID,
    VOICE,
    FakeBot,
    FakeReceiver,
    connected,
    disconnected,
    ready,
    rest_voice_state,
    run,
    settle,
)

from bostie import voice
from bostie.voice.connection import ContextVoice, VoiceDelivery

if TYPE_CHECKING:
    from bostie._native import FrameItem

DEAFENED = (
    f"a moderator deafened the bot in guild {GUILD_ID}, so Discord sends it no audio"
)


def frame(offset: int, age: float = 0.0, queued: float = 0.0) -> "FrameItem":
    return ("frame", USER_ID, 77, offset, offset, 960, age, b"\x01\x00", False, queued)


async def open_receiver(
    bot: FakeBot,
) -> tuple[voice.Receiver[voice.Frame], FakeReceiver]:
    vc = await connected(bot)
    return vc.receive(), bot.core.receivers[-1]


def test_receivers_take_their_settings_from_the_defaults_unless_given() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        vc.receive()
        packets = vc.receive(
            voice.Packets(),
            users={USER_ID + 1, USER_ID},
            exclude=[USER_ID],
            include_self=True,
            buffer=5.0,
            markers=True,
            overflow="drop_oldest",
            stall="wait",
        )
        mixed = vc.receive(voice.PCM(16_000, 1, "f32le"), jitter=0.0, mix=True)
        vc.receive(voice.Opus(), users=())
        assert_type(
            packets, voice.Receiver[voice.Packet | voice.SpeechStart | voice.SpeechStop]
        )
        assert_type(mixed, voice.Receiver[voice.Frame])
        common: dict[str, Any] = {
            "rate": 48_000,
            "channels": 2,
            "sample": "s16le",
            "users": None,
            "exclude": [],
            "include_self": False,
            "buffer": 1.5,
            "jitter": 0.06,
            "markers": False,
            "mix": False,
            "overflow": "drop_newest",
            "stall": "end",
        }
        assert [call[2] for call in bot.calls("voice_receive")] == [
            {**common, "kind": "pcm"},
            {
                **common,
                "kind": "packets",
                "users": [USER_ID, USER_ID + 1],
                "exclude": [USER_ID],
                "include_self": True,
                "buffer": 5.0,
                "markers": True,
                "overflow": "drop_oldest",
                "stall": "wait",
            },
            {
                **common,
                "kind": "pcm",
                "rate": 16_000,
                "channels": 1,
                "sample": "f32le",
                "jitter": 0.0,
                "mix": True,
            },
            {**common, "kind": "opus", "users": []},
        ]
        assert (packets.users, packets.exclude, packets.jitter, packets.buffer) == (
            frozenset({USER_ID, USER_ID + 1}),
            frozenset({USER_ID}),
            0.06,
            5.0,
        )

    run(
        scenario,
        defaults=voice.Defaults(
            receive_buffer=1.5,
            jitter=0.06,
            overflow="drop_newest",
            receive_stall="end",
        ),
    )


def test_items_become_frames_packets_and_speech_aged_against_one_reading() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        rx = vc.receive(markers=True)
        bot.core.receivers[-1].items.extend(
            [
                ("speech", USER_ID, 77, True, 0, 0.5, 0.0, 0.0),
                ("frame", USER_ID, 77, 1000, 0, 960, 0.25, b"pcm", False, 0.0),
                ("frame", None, None, None, 960, 960, 0.125, b"", True, 0.0),
                ("packet", USER_ID, 77, 9, 1960, 0.0625, b"opus", [(1, b"\x85")], 0.0),
                ("speech", USER_ID, 77, False, 1920, 0.03125, 0.25, 0.0),
            ]
        )
        before = time.monotonic()
        items: list[object] = [rx.get_nowait() for _ in range(5)]
        after = time.monotonic()
        start = items[0]
        assert isinstance(start, voice.SpeechStart)
        now = start.at + 0.5
        assert before <= now <= after
        assert items == [
            voice.SpeechStart(
                guild_id=GUILD_ID, user_id=USER_ID, ssrc=77, at=now - 0.5, offset=0
            ),
            voice.Frame(USER_ID, 77, 1000, 0, 960, now - 0.25, b"pcm", False),
            voice.Frame(None, None, None, 960, 960, now - 0.125, b"", True),
            voice.Packet(USER_ID, 77, 9, 1960, now - 0.0625, b"opus", ((1, b"\x85"),)),
            voice.SpeechStop(
                guild_id=GUILD_ID,
                user_id=USER_ID,
                ssrc=77,
                at=now - 0.03125,
                duration=0.25,
                offset=1920,
            ),
        ]

    run(scenario)


def test_iteration_lets_other_tasks_run_after_each_full_batch() -> None:
    async def scenario(bot: FakeBot) -> None:
        rx, native = await open_receiver(bot)
        offsets: list[int] = []
        seen: list[int] = []

        async def consume() -> None:
            async for item in rx:
                offsets.append(item.offset)

        async def watch() -> None:
            while True:
                seen.append(len(offsets))
                await asyncio.sleep(0)

        consumer = asyncio.ensure_future(consume())
        watcher = asyncio.ensure_future(watch())
        await settle()
        native.feed(*(frame(960 * index) for index in range(600)))
        await settle()
        bot.deliver(disconnected("removed"))
        await consumer
        watcher.cancel()
        assert offsets == [960 * index for index in range(600)]
        assert 256 in seen

    run(scenario)


@pytest.mark.parametrize(
    ("ended", "ending", "reason"),
    [
        ("disconnected", disconnected("removed"), voice.ClosedReason.DISCONNECTED),
        (
            "moved",
            ("voice_moved", GUILD_ID, VOICE, OTHER_VOICE, False),
            voice.ClosedReason.MOVED,
        ),
        (None, disconnected("removed"), voice.ClosedReason.DISCONNECTED),
    ],
)
def test_iteration_ends_after_the_queued_items_with_the_reason(
    ended: Literal["disconnected", "moved"] | None,
    ending: VoiceDelivery,
    reason: voice.ClosedReason,
) -> None:
    async def scenario(bot: FakeBot) -> None:
        rx, native = await open_receiver(bot)
        native.items.extend(frame(960 * index) for index in range(3))
        native.counters = (3, 1, 2)
        if ended is not None:
            native.end(ended)
        bot.deliver(ending)
        assert [item.offset async for item in rx] == [0, 960, 1920]
        assert (rx.closed, rx.closed_reason, rx.stats()) == (
            True,
            reason,
            voice.ReceiverStats(3, 1, 2),
        )

    run(scenario)


def test_leaving_the_block_closes_and_ends_a_wait_without_another_read() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        native: FakeReceiver | None = None
        with pytest.raises(LookupError):
            async with vc.receive() as rx:
                native = bot.core.receivers[-1]
                native.counters = (3, 1, 2)
                waiting = asyncio.ensure_future(anext(rx, None))
                await settle()
                raise LookupError
        assert await waiting is None
        assert native is not None
        assert (native.closed, native.reads) == (True, [256])
        assert (rx.closed, rx.closed_reason) == (True, voice.ClosedReason.CLOSED)
        rx.clear()
        rx.close()
        assert (rx.queued, rx.stats()) == (0.0, voice.ReceiverStats(3, 1, 2))

    run(scenario)


def test_a_cancelled_wait_loses_no_item() -> None:
    async def scenario(bot: FakeBot) -> None:
        rx, native = await open_receiver(bot)
        cancelled = asyncio.ensure_future(anext(rx))
        await settle()
        cancelled.cancel()
        await settle()
        waiting = asyncio.ensure_future(anext(rx))
        await settle()
        native.feed(frame(0))
        assert (await waiting).offset == 0

    run(scenario)


def test_a_receiver_mark_resolves_on_delivery_or_false_when_it_ends() -> None:
    async def scenario(bot: FakeBot) -> None:
        rx, native = await open_receiver(bot)
        passed, pending = rx.mark(), rx.mark()
        bot.deliver(("voice_marker", native.id, 1, True))
        assert await passed
        bot.deliver(disconnected("removed"))
        assert (await pending, await rx.mark()) == (False, False)

    run(scenario)


def test_get_nowait_raises_empty_then_receiver_closed_once_drained() -> None:
    async def scenario(bot: FakeBot) -> None:
        rx, native = await open_receiver(bot)
        with pytest.raises(voice.Empty):
            rx.get_nowait()
        native.items.append(frame(0))
        native.end("disconnected")
        assert rx.get_nowait().offset == 0
        with pytest.raises(voice.ReceiverClosed):
            rx.get_nowait()
        assert rx.closed_reason is voice.ClosedReason.DISCONNECTED

    run(scenario)


def test_queued_counts_from_queueing_and_clear_and_close_drop_the_items() -> None:
    async def scenario(bot: FakeBot) -> None:
        rx, native = await open_receiver(bot)
        native.lag = 5.0
        assert rx.queued == 5.0
        native.items.extend(
            [frame(0, age=9.0, queued=2.0), frame(960, age=9.0, queued=1.0)]
        )
        rx.get_nowait()
        assert 1.0 <= rx.queued < 2.0
        rx.clear()
        native.lag = 0.0
        assert (rx.queued, native.clears) == (0.0, 1)
        with pytest.raises(voice.Empty):
            rx.get_nowait()
        native.items.extend([frame(1920), frame(2880)])
        rx.get_nowait()
        rx.close()
        with pytest.raises(voice.ReceiverClosed):
            rx.get_nowait()

    run(scenario)


@pytest.mark.parametrize(
    ("steps", "warnings"),
    [
        (["server_deafen", "receive"], [DEAFENED]),
        (["receive", "server_deafen", "server_deafen"], [DEAFENED]),
        (["self_deafen", "receive", "self_deafen"], []),
        (["receive", "close", "server_deafen"], []),
    ],
)
def test_a_moderator_deafen_warns_once_per_receive_and_a_self_deafen_never(
    caplog: pytest.LogCaptureFixture, steps: list[str], warnings: list[str]
) -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        opened: list[voice.Receiver[voice.Frame]] = []
        for step in steps:
            match step:
                case "receive":
                    opened.append(vc.receive())
                case "close":
                    opened.pop().close()
                case "self_deafen":
                    bot.deliver(("voice_self", GUILD_ID, False, True, False, False))
                case "server_deafen":
                    bot.deliver(("voice_deaf", GUILD_ID, True))
        assert vc.deaf is ("server_deafen" in steps)

    with caplog.at_level(logging.WARNING, "bostie.voice"):
        run(scenario)
    assert [record.getMessage() for record in caplog.records] == warnings


def test_speech_updates_speaking_before_its_event_and_disconnect_stops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        seen: list[tuple[voice.VoiceEvent, dict[int, float]]] = []
        monkeypatch.setattr(
            bot, "_emit", lambda event: seen.append((event, dict(vc.speaking)))
        )
        now = time.monotonic()
        with monkeypatch.context() as frozen:
            frozen.setattr(time, "monotonic", lambda: now)
            bot.deliver(("voice_speech", GUILD_ID, USER_ID, 11, True, 0.5, 0.0))
            bot.deliver(("voice_speech", GUILD_ID, USER_ID + 1, 12, True, 0.25, 0.0))
            bot.deliver(("voice_speech", GUILD_ID, USER_ID + 1, 12, False, 0.125, 0.25))
        bot.deliver(disconnected("removed"))
        ended = time.monotonic()
        bot.deliver(("voice_speech", GUILD_ID, USER_ID, 11, True, 0.0, 0.0))
        stop = seen[3][0]
        assert isinstance(stop, voice.SpeechStop)
        assert now <= stop.at <= ended
        assert seen[:4] == [
            (
                voice.SpeechStart(
                    guild_id=GUILD_ID, user_id=USER_ID, ssrc=11, at=now - 0.5
                ),
                {USER_ID: now - 0.5},
            ),
            (
                voice.SpeechStart(
                    guild_id=GUILD_ID, user_id=USER_ID + 1, ssrc=12, at=now - 0.25
                ),
                {USER_ID: now - 0.5, USER_ID + 1: now - 0.25},
            ),
            (
                voice.SpeechStop(
                    guild_id=GUILD_ID,
                    user_id=USER_ID + 1,
                    ssrc=12,
                    at=now - 0.125,
                    duration=0.25,
                ),
                {USER_ID: now - 0.5},
            ),
            (
                voice.SpeechStop(
                    guild_id=GUILD_ID,
                    user_id=USER_ID,
                    ssrc=11,
                    at=stop.at,
                    duration=stop.at - (now - 0.5),
                ),
                {},
            ),
        ]
        assert [type(event) for event, _ in seen[4:]] == [voice.StateChange]
        assert vc.speaking == {}

    run(scenario)


def test_speech_settings_reach_the_core_at_connect_and_on_assignment() -> None:
    async def scenario(bot: FakeBot) -> None:
        vc = await connected(bot)
        vc.speech_hangover = 0.75
        vc.speech_activity = True
        bot.rest.answers["GET", f"/guilds/{OTHER_GUILD_ID}/voice-states/{USER_ID}"] = (
            200,
            rest_voice_state(NO_BITRATE, guild_id=OTHER_GUILD_ID),
        )
        ctx = ContextVoice(bot.voice, OTHER_GUILD_ID, USER_ID)
        joining = asyncio.ensure_future(
            ctx.connect(speech_hangover=0.5, speech_activity=True)
        )
        await settle()
        bot.deliver(ready(OTHER_GUILD_ID))
        await joining
        assert [call[1] for call in bot.calls("voice_speech_hangover")] == [
            (GUILD_ID, 0.3),
            (GUILD_ID, 0.75),
            (OTHER_GUILD_ID, 0.5),
        ]
        assert [call[1] for call in bot.calls("voice_speech_activity")] == [
            (GUILD_ID, False),
            (GUILD_ID, True),
            (OTHER_GUILD_ID, True),
        ]
        assert (vc.speech_hangover, vc.speech_activity) == (0.75, True)

    run(scenario, defaults=voice.Defaults(speech_hangover=0.3, speech_activity=False))
