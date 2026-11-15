import asyncio
import contextlib
import logging
import struct
from collections.abc import AsyncIterator
from typing import Any, Literal, assert_type

import pytest

from bostie import Bot, voice
from bostie.testing import FakeDiscord, payloads, run, running, settle

GUILD_ID = int(payloads.voice_channel()["guild_id"])
VOICE_ID = int(payloads.voice_channel()["id"])
OTHER_ID = VOICE_ID + 1
FRIEND_ID = 1_100_000_000_000_000_001
SSRC = 4321
SPOKEN = voice.OpusEncoder().encode(bytes(3840 * 50))
MANY = voice.OpusEncoder().encode(bytes(3840 * 600))
BURST = 50
REPORT = struct.pack(">BBHI", 0x80, 201, 1, SSRC)
DEAFENED = (
    f"a moderator deafened the bot in guild {GUILD_ID}, so Discord sends it no audio"
)

type Item = voice.Packet | voice.SpeechStart | voice.SpeechStop | voice.UserLeave


@contextlib.asynccontextmanager
async def started(fake: FakeDiscord, **options: Any) -> AsyncIterator[Bot]:
    fake.channels[str(OTHER_ID)] = payloads.voice_channel(id=str(OTHER_ID))
    bot = Bot(api_url=fake.api_url, **options)
    async with running(bot, fake):
        yield bot


async def connected(bot: Bot, fake: FakeDiscord) -> voice.VoiceConnection:
    vc = await bot.voice.connect(VOICE_ID)
    await fake.voice.speaking(FRIEND_ID, SSRC, 1)
    return vc


async def until_queued(bot: Bot, fake: FakeDiscord, rx: voice.Receiver[Any]) -> None:
    await asyncio.gather(bot.wait_for(voice.RtcpPacket), fake.voice.send_rtcp(REPORT))
    assert await rx.mark()


async def moved(bot: Bot, fake: FakeDiscord, channel_id: int) -> None:
    await asyncio.gather(bot.wait_for(voice.Moved), fake.move_bot(channel_id))


def test_receivers_take_their_settings_from_the_defaults_unless_given() -> None:
    defaults = voice.Defaults(
        receive_buffer=1.5, jitter=0.06, overflow="drop_newest", receive_stall="end"
    )

    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake, voice=defaults) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            plain = vc.receive()
            packets = vc.receive(
                voice.Packets(),
                users={FRIEND_ID + 1, FRIEND_ID},
                exclude=[FRIEND_ID],
                include_self=True,
                buffer=5.0,
                markers=True,
                overflow="drop_oldest",
                stall="wait",
            )
            mixed = vc.receive(voice.PCM(16_000, 1, "f32le"), jitter=0.0, mix=True)

            assert_type(packets, voice.Receiver[Item])
            assert_type(mixed, voice.Receiver[voice.Frame])
            assert (plain.users, plain.exclude, plain.include_self, plain.markers) == (
                None,
                frozenset(),
                False,
                False,
            )
            assert (plain.buffer, plain.jitter, plain.overflow, plain.stall) == (
                1.5,
                0.06,
                "drop_newest",
                "end",
            )
            assert (packets.users, packets.exclude, packets.buffer) == (
                frozenset({FRIEND_ID, FRIEND_ID + 1}),
                frozenset({FRIEND_ID}),
                5.0,
            )
            assert (packets.overflow, packets.stall, packets.include_self) == (
                "drop_oldest",
                "wait",
                True,
            )
            assert (mixed.format, mixed.jitter, mixed.mix) == (
                voice.PCM(16_000, 1, "f32le"),
                0.0,
                True,
            )

    run(scenario, voice=True)


def test_packets_carry_their_rtp_fields_and_markers_their_user() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await connected(bot, fake)
            rx = vc.receive(voice.Packets(), markers=True)
            await fake.voice.send_audio(
                SSRC, SPOKEN[:1], sequence=9, timestamp=1960, extension=b"\x10\x85"
            )
            packet = await anext(
                item async for item in rx if isinstance(item, voice.Packet)
            )
            await fake.voice.client_disconnect(FRIEND_ID)
            leave = await anext(
                item async for item in rx if isinstance(item, voice.UserLeave)
            )

        assert (packet.user_id, packet.ssrc, packet.sequence, packet.timestamp) == (
            FRIEND_ID,
            SSRC,
            9,
            1960,
        )
        assert (packet.opus, packet.extensions) == (SPOKEN[0], ((1, b"\x85"),))
        assert leave == voice.UserLeave(guild_id=GUILD_ID, user_id=FRIEND_ID)

    run(scenario, voice=True)


def test_iteration_lets_other_tasks_run_after_each_full_batch() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await connected(bot, fake)
            rx = vc.receive(voice.Packets(), buffer=60.0)
            for start in range(0, len(MANY), BURST):
                burst = MANY[start : start + BURST]
                await fake.voice.send_audio(
                    SSRC, burst, sequence=start, timestamp=start * 960
                )
                await until_queued(bot, fake, rx)
            sequences: list[int] = []
            seen: list[int] = []

            async def watch() -> None:
                while True:
                    seen.append(len(sequences))
                    await asyncio.sleep(0)

            watcher = asyncio.create_task(watch())
            async for packet in rx:
                sequences.append(packet.sequence)
                if len(sequences) == len(MANY):
                    break
            watcher.cancel()

            assert sequences == list(range(len(MANY)))
            assert 256 in seen

    run(scenario, voice=True)


@pytest.mark.parametrize(
    ("ending", "reason"),
    [
        ("disconnect", voice.ClosedReason.DISCONNECTED),
        ("move", voice.ClosedReason.MOVED),
    ],
)
def test_iteration_ends_after_the_queued_items_with_the_reason(
    ending: str, reason: voice.ClosedReason
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await connected(bot, fake)
            rx = vc.receive(voice.Packets())
            await fake.voice.send_audio(SSRC, SPOKEN[:3])
            await until_queued(bot, fake, rx)
            if ending == "disconnect":
                await vc.disconnect()
            else:
                await moved(bot, fake, OTHER_ID)

            assert [packet.sequence async for packet in rx] == [0, 1, 2]
            assert (rx.closed, rx.closed_reason) == (True, reason)
            assert rx.stats() == voice.ReceiverStats(3, 0, 0)

    run(scenario, voice=True)


def test_get_nowait_raises_empty_then_receiver_closed_once_drained() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await connected(bot, fake)
            rx = vc.receive(voice.Packets())
            with pytest.raises(voice.Empty):
                rx.get_nowait()
            await fake.voice.send_audio(SSRC, SPOKEN[:1])
            await until_queued(bot, fake, rx)
            await vc.disconnect()

            assert rx.get_nowait().sequence == 0
            with pytest.raises(voice.ReceiverClosed):
                rx.get_nowait()
            assert rx.closed_reason is voice.ClosedReason.DISCONNECTED

    run(scenario, voice=True)


def test_leaving_the_block_closes_and_ends_a_wait_without_another_read() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            rx = vc.receive()
            waiting = asyncio.create_task(anext(rx, None))
            await settle()

            async def leave_with_an_error() -> None:
                async with rx:
                    raise LookupError

            with pytest.raises(LookupError):
                await leave_with_an_error()
            stats = rx.stats()
            rx.clear()
            rx.close()

            assert await waiting is None
            assert (rx.closed, rx.closed_reason) == (True, voice.ClosedReason.CLOSED)
            assert (rx.queued, rx.stats()) == (0.0, stats)

    run(scenario, voice=True)


def test_a_cancelled_wait_loses_no_item() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await connected(bot, fake)
            rx = vc.receive(voice.Packets())
            cancelled = asyncio.create_task(anext(rx))
            await settle()
            cancelled.cancel()
            await settle()
            waiting = asyncio.create_task(anext(rx))
            await settle()
            await fake.voice.send_audio(SSRC, SPOKEN[:1])

            assert (await waiting).sequence == 0

    run(scenario, voice=True)


def test_a_receiver_mark_is_false_once_the_connection_ended() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            rx = vc.receive()
            live = await rx.mark()
            await vc.disconnect()

            assert (live, await rx.mark()) == (True, False)

    run(scenario, voice=True)


def test_queued_counts_the_lag_and_clear_and_close_drop_the_items() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await connected(bot, fake)
            rx = vc.receive(voice.Packets())
            await fake.voice.send_audio(SSRC, SPOKEN[:3])
            await until_queued(bot, fake, rx)
            rx.get_nowait()
            lag = rx.queued
            rx.clear()
            cleared = rx.queued
            with pytest.raises(voice.Empty):
                rx.get_nowait()
            rx.close()
            with pytest.raises(voice.ReceiverClosed):
                rx.get_nowait()

            assert (lag > 0.0, cleared) == (True, 0.0)

    run(scenario, voice=True)


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
    caplog: pytest.LogCaptureFixture,
    steps: list[Literal["receive", "close", "self_deafen", "server_deafen"]],
    warnings: list[str],
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            opened: list[voice.Receiver[voice.Frame]] = []
            for step in steps:
                match step:
                    case "receive":
                        opened.append(vc.receive())
                    case "close":
                        opened.pop().close()
                    case "self_deafen":
                        await vc.edit(self_deaf=True)
                    case "server_deafen":
                        fake.deaf = True
                        elsewhere = OTHER_ID if vc.channel_id == VOICE_ID else VOICE_ID
                        await moved(bot, fake, elsewhere)
            assert vc.deaf is ("server_deafen" in steps)

    with caplog.at_level(logging.WARNING, "bostie.voice"):
        run(scenario, voice=True)
    assert [record.getMessage() for record in caplog.records] == warnings


def test_speech_updates_speaking_before_its_event_and_disconnect_stops_it() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        connections: list[voice.VoiceConnection] = []
        seen: list[tuple[voice.VoiceEvent, dict[int, float]]] = []

        @bot.listen
        async def on_speech(event: voice.SpeechStart | voice.SpeechStop) -> None:
            seen.append((event, dict(connections[0].speaking)))

        async with running(bot, fake):
            connections.append(vc := await connected(bot, fake))
            start, _ = await asyncio.gather(
                bot.wait_for(voice.SpeechStart), fake.voice.send_audio(SSRC, SPOKEN)
            )
            await vc.disconnect()

        (first, during), (stop, after) = seen
        assert (first, during) == (start, {FRIEND_ID: start.at})
        assert isinstance(stop, voice.SpeechStop)
        assert (stop.user_id, stop.ssrc, after) == (FRIEND_ID, SSRC, {})
        assert stop.duration == pytest.approx(stop.at - start.at)
        assert vc.speaking == {}

    run(scenario, voice=True)


def test_speech_events_follow_speech_activity_from_the_connect_on() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, voice=voice.Defaults(speech_activity=False))
        starts: list[voice.SpeechStart] = []

        @bot.listen
        async def on_start(event: voice.SpeechStart) -> None:
            starts.append(event)

        async with running(bot, fake):
            vc = await connected(bot, fake)
            rx = vc.receive(markers=True)
            await fake.voice.send_audio(SSRC, SPOKEN[:5])
            marked = await anext(rx)
            silent = list(starts)
            vc.speech_activity = True
            await asyncio.gather(
                bot.wait_for(voice.SpeechStart),
                fake.voice.send_audio(SSRC, SPOKEN[5:10], sequence=5, timestamp=4800),
            )

        assert isinstance(marked, voice.SpeechStart)
        assert silent == []

    run(scenario, voice=True)
