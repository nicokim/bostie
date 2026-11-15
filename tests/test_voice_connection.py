import asyncio
import contextlib
import hashlib
import inspect
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from bostie import Bot, voice
from bostie.enums import SpeakingFlags
from bostie.events import GuildCreate
from bostie.testing import FakeDiscord, payloads, run, running, settle
from bostie.testing.payloads import Json

GUILD_ID = int(payloads.voice_channel()["guild_id"])
VOICE_ID = int(payloads.voice_channel()["id"])
STAGE_ID = int(payloads.stage_channel()["id"])
OTHER_ID = VOICE_ID + 1
ELSEWHERE_ID = VOICE_ID + 2
FRIEND_ID = 1_100_000_000_000_000_001
OTHER_FRIEND_ID = FRIEND_ID + 1
FRIEND_SSRC = 4321
SILENCE = b"\xf8\xff\xfe"
SAMPLE = b"\x00\x00\x00\x00"
NOISE = hashlib.shake_256(b"noise").digest(48_000 * 4 // 5)


@contextlib.asynccontextmanager
async def started(fake: FakeDiscord, **options: Any) -> AsyncIterator[Bot]:
    bot = Bot(api_url=fake.api_url, **options)
    async with running(bot, fake):
        yield bot


def joined(
    channel_id: int | None, *, self_mute: bool = False, self_deaf: bool = False
) -> Json:
    return {
        "guild_id": str(GUILD_ID),
        "channel_id": None if channel_id is None else str(channel_id),
        "self_mute": self_mute,
        "self_deaf": self_deaf,
    }


def sent(fake: FakeDiscord, op: int) -> list[Any]:
    payloads = []
    while not fake.gateway_received.empty():
        payload = fake.gateway_received.get_nowait()
        if payload["op"] == op:
            payloads.append(payload["d"])
    return payloads


def add_voice_channel(fake: FakeDiscord, channel_id: int, **fields: Any) -> Json:
    channel = payloads.voice_channel(id=str(channel_id), **fields)
    fake.channels[str(channel_id)] = channel
    return channel


async def awaited(result: object) -> None:
    if inspect.isawaitable(result):
        await result


async def moved(bot: Bot, fake: FakeDiscord, channel_id: int) -> None:
    await asyncio.gather(bot.wait_for(voice.Moved), fake.move_bot(channel_id))


@pytest.mark.parametrize(
    ("call", "option"),
    [
        ("sink", "jitter"),
        ("play", "overflow"),
        ("queue", "timeout"),
        ("receive", "volume"),
        ("packets", "jitter"),
        ("move", "self_mute"),
        ("edit", "stage"),
    ],
)
def test_options_a_call_does_not_take_raise_type_error(call: str, option: str) -> None:
    options: dict[str, Any] = {option: 0.1}

    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            attempts: dict[str, Callable[[], object]] = {
                "sink": lambda: vc.sink(**options),
                "play": lambda: vc.play(voice.File("a.ogg"), **options),
                "queue": lambda: voice.Queue(vc, **options),
                "receive": lambda: vc.receive(**options),
                "packets": lambda: vc.receive(voice.Packets(), **options),
                "move": lambda: vc.move(OTHER_ID, **options),
                "edit": lambda: vc.edit(**options),
            }
            with pytest.raises(TypeError, match=option):
                await awaited(attempts[call]())
            assert vc.sinks == ()
        assert sent(fake, 4) == [joined(VOICE_ID), joined(None)]

    run(scenario, voice=True)


def test_vc_stage_works_only_on_a_stage_channel() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            with pytest.raises(ValueError, match="is not a stage channel"):
                await vc.stage("speak")
            await vc.disconnect()
            await (await bot.voice.connect(STAGE_ID)).stage("listen")
        assert [r.json for r in fake.requests if r.method == "PATCH"] == [
            {"channel_id": STAGE_ID, "suppress": True}
        ]

    run(scenario, voice=True)


def test_wait_until_sees_every_state_and_raises_when_the_connection_ends() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            resuming = asyncio.create_task(vc.wait_until(voice.State.RESUMING))
            await settle()
            vc.reconnect()
            await resuming
            await vc.wait_until(voice.State.READY)
            reconnecting = asyncio.create_task(vc.wait_until(voice.State.RECONNECTING))
            ended = asyncio.create_task(vc.wait_until(voice.State.DISCONNECTED))
            await settle()
            await vc.disconnect()
            with pytest.raises(voice.VoiceNotConnected):
                await reconnecting
            await ended
            await vc.wait_until(voice.State.DISCONNECTED)

    run(scenario, voice=True)


def test_members_follow_joins_and_leaves_and_a_known_member_joins_once() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        async with started(fake) as bot:
            joins: list[int] = []

            @bot.listen
            async def on_join(event: voice.UserJoin) -> None:
                joins.append(event.user_id)

            vc = await bot.voice.connect(VOICE_ID)
            await fake.voice.clients_connect([FRIEND_ID, OTHER_FRIEND_ID])
            await fake.voice.client_disconnect(OTHER_FRIEND_ID)
            await asyncio.gather(
                bot.wait_for(
                    voice.UserJoin, check=lambda e: e.user_id == FRIEND_ID + 2
                ),
                fake.voice.clients_connect([FRIEND_ID, FRIEND_ID + 2]),
            )
            members = vc.members
            await moved(bot, fake, OTHER_ID)

            assert members == {FRIEND_ID, FRIEND_ID + 2}
            assert joins == [FRIEND_ID, OTHER_FRIEND_ID, FRIEND_ID + 2]
            assert vc.members == frozenset()

    run(scenario, voice=True)


@pytest.mark.parametrize(
    ("flag", "warning"),
    [
        ("mute", "it is server muted"),
        ("suppress", "it is suppressed on this stage; use vc.stage('request')"),
    ],
)
def test_audio_into_a_muted_connection_warns_once(
    caplog: pytest.LogCaptureFixture, flag: str, warning: str
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            await vc.sink().write(SAMPLE)
            heard = list(caplog.records)
            setattr(fake, flag, True)
            await moved(bot, fake, OTHER_ID)
            first, second = vc.sink(), vc.sink()
            await first.write(SAMPLE)
            await first.write(SAMPLE)
            await second.write(SAMPLE)

        assert heard == []

    with caplog.at_level(logging.WARNING, "bostie.voice"):
        run(scenario, voice=True)
    assert [record.getMessage() for record in caplog.records] == [
        f"nobody hears the bot in guild {GUILD_ID}: {warning}"
    ]


def set_encoder(vc: voice.VoiceConnection) -> None:
    vc.encoder = voice.EncoderSettings(bitrate=96_000)


def set_speaking_flags(vc: voice.VoiceConnection) -> None:
    vc.speaking_flags = SpeakingFlags.SOUNDSHARE


def set_speech_hangover(vc: voice.VoiceConnection) -> None:
    vc.speech_hangover = 1.0


def set_stall_after(vc: voice.VoiceConnection) -> None:
    vc.stall_after = 1.0


def set_speech_activity(vc: voice.VoiceConnection) -> None:
    vc.speech_activity = False


AFTER_THE_END: dict[str, Callable[[voice.VoiceConnection], object]] = {
    "sink": lambda vc: vc.sink(),
    "play": lambda vc: vc.play(voice.File("a.ogg")),
    "move": lambda vc: vc.move(OTHER_ID),
    "edit": lambda vc: vc.edit(self_mute=True),
    "stage": lambda vc: vc.stage("speak"),
    "reconnect": lambda vc: vc.reconnect(),
    "encoder": set_encoder,
    "speaking_flags": set_speaking_flags,
    "receive": lambda vc: vc.receive(),
    "speech_hangover": set_speech_hangover,
    "speech_activity": set_speech_activity,
    "stall_after": set_stall_after,
    "wait_until": lambda vc: vc.wait_until(voice.State.READY),
    "verification_code": lambda vc: vc.dave.verification_code(FRIEND_ID),
}


@pytest.mark.parametrize("name", AFTER_THE_END)
def test_after_the_end_calls_raise_voice_not_connected(name: str) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            await vc.disconnect()
            with pytest.raises(voice.VoiceNotConnected, match="has ended"):
                await awaited(AFTER_THE_END[name](vc))

    run(scenario, voice=True)


def test_after_the_end_reads_return_the_last_values() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            await asyncio.gather(
                bot.wait_for(voice.SpeakingUpdate),
                fake.voice.speaking(FRIEND_ID, FRIEND_SSRC, 1),
            )
            ssrcs, dave = vc.ssrcs, vc.dave
            await vc.disconnect()

            assert ssrcs == {FRIEND_SSRC: FRIEND_ID}
            assert (vc.ssrcs, vc.dave) == (ssrcs, dave)
            assert vc.latency == voice.Latency(None, None)
            assert bot.voice.connections == ()

    run(scenario, voice=True)


async def packet_size(fake: FakeDiscord, vc: voice.VoiceConnection) -> float:
    start = len(fake.voice.packets)
    async with vc.sink(buffer=1.0) as out:
        out.write_nowait(NOISE)
    await fake.voice.wait_for_packets(start + 10)
    sizes = [len(p.payload) for p in fake.voice.packets[start:] if p.payload != SILENCE]
    return sum(sizes) / len(sizes)


def test_the_bitrate_follows_the_channel_across_a_move_until_the_encoder_sets_one() -> (
    None
):
    async def scenario(fake: FakeDiscord) -> None:
        guild = payloads.guild()
        guild["channels"].append(add_voice_channel(fake, OTHER_ID, bitrate=16_000))
        async with started(fake) as bot:
            await asyncio.gather(
                bot.wait_for(GuildCreate), fake.dispatch("GUILD_CREATE", guild)
            )
            vc = await bot.voice.connect(VOICE_ID)
            wide = await packet_size(fake, vc)
            await moved(bot, fake, OTHER_ID)
            await vc.wait_until(voice.State.READY)
            await settle()
            narrow = await packet_size(fake, vc)
            vc.encoder = voice.EncoderSettings(bitrate=64_000)
            chosen = await packet_size(fake, vc)

        assert narrow < wide / 2
        assert chosen > narrow * 2

    run(scenario, voice=True)


def test_speaking_flags_need_at_least_one_flag() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            with pytest.raises(ValueError, match="at least one flag"):
                vc.speaking_flags = SpeakingFlags(0)
            assert vc.speaking_flags is SpeakingFlags.MICROPHONE

    run(scenario, voice=True)


def test_move_waits_for_the_new_channel_and_ready() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID, self_deaf=True)
            await vc.move(OTHER_ID)
            assert (vc.channel_id, vc.state) == (OTHER_ID, voice.State.READY)
        assert sent(fake, 4)[:2] == [
            joined(VOICE_ID, self_deaf=True),
            joined(OTHER_ID, self_deaf=True),
        ]

    run(scenario, voice=True)


def test_a_move_that_times_out_keeps_the_connection_where_it_was() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, OTHER_ID)
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            release = fake.hold_voice_state()
            with pytest.raises(voice.VoiceConnectError) as raised:
                await vc.move(OTHER_ID, timeout=0.05)
            release.set()

            assert raised.value.reason is voice.DisconnectReason.TIMEOUT
            assert raised.value.detail == (
                f"no move to {OTHER_ID}; the channel may be full or the bot lacks "
                "CONNECT"
            )
            assert (vc.channel_id, vc.state) == (VOICE_ID, voice.State.READY)

    run(scenario, voice=True)


def test_a_move_to_another_guilds_channel_is_refused() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        add_voice_channel(fake, ELSEWHERE_ID, guild_id=str(GUILD_ID + 1))
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            with pytest.raises(ValueError, match="in another guild"):
                await vc.move(ELSEWHERE_ID)
        assert sent(fake, 4) == [joined(VOICE_ID), joined(None)]

    run(scenario, voice=True)


def test_edit_sends_the_flags_and_returns_once_discord_applied_them() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID, self_deaf=True)
            await fake.next_voice_state_update()
            release = fake.hold_voice_state()
            editing = asyncio.create_task(vc.edit(self_mute=True))
            asked = await fake.next_voice_state_update()
            waited = not editing.done()
            release.set()
            await editing
            await vc.edit(self_deaf=False)

            assert waited
            assert asked == joined(VOICE_ID, self_mute=True, self_deaf=True)
            assert (vc.self_mute, vc.self_deaf) == (True, False)
            assert await fake.next_voice_state_update() == joined(
                VOICE_ID, self_mute=True
            )

    run(scenario, voice=True)


def test_edit_raises_on_timeout_and_when_the_connection_ends() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            release = fake.hold_voice_state()
            with pytest.raises(TimeoutError):
                await vc.edit(self_mute=True, timeout=0.01)
            editing = asyncio.create_task(vc.edit(self_deaf=True))
            await settle()
            await fake.kick_bot()
            with pytest.raises(voice.VoiceNotConnected, match="has ended"):
                await editing
            release.set()

    run(scenario, voice=True)


def test_disconnect_returns_once_ended_and_is_idempotent() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            ended = asyncio.create_task(
                bot.wait_for(
                    voice.StateChange,
                    check=lambda event: event.new is voice.State.DISCONNECTED,
                )
            )
            await vc.disconnect()
            await vc.disconnect()

            assert vc.state is voice.State.DISCONNECTED
            assert (await ended).reason is voice.DisconnectReason.REQUESTED
        assert sent(fake, 4) == [joined(VOICE_ID), joined(None)]

    run(scenario, voice=True)


def test_close_ends_a_connection_the_core_has_not_ended_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unanswered(vc: voice.VoiceConnection) -> None:
        return None

    monkeypatch.setattr(voice.VoiceConnection, "disconnect", unanswered)

    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as bot:
            vc = await bot.voice.connect(VOICE_ID)
            ended = asyncio.create_task(bot.wait_for(voice.StateChange))
            await bot.voice.close()

            assert bot.voice.connections == ()
            assert vc.state is voice.State.DISCONNECTED
            assert await ended == voice.StateChange(
                guild_id=GUILD_ID,
                old=voice.State.READY,
                new=voice.State.DISCONNECTED,
                reason=voice.DisconnectReason.CLOSED,
                code=None,
                detail="the bot closed",
            )

    run(scenario, voice=True)


def test_deliveries_for_unknown_handles_and_guilds_are_ignored() -> None:
    voices = Bot().voice
    voices.handle(("voice_wake", 12345))
    voices.handle(("voice_marker", 12345, 1, True))
    voices.handle(("voice_state", 999, "ready", None, None, ""))
    assert voices.connections == ()
