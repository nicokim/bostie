import asyncio
import contextlib
import logging
import math
import os
import struct
import sys
from collections.abc import AsyncIterator, Callable

from bostie import Bot, voice
from bostie.events import VoiceServerUpdate
from bostie.voice import State

VARIABLES = ("BOSTIE_TOKEN", "BOSTIE_LIVE_GUILD", "BOSTIE_LIVE_VOICE")
HUMAN_TIMEOUT = 120
GATEWAY_TIMEOUT = 30
VOICE_TIMEOUT = 15
SAMPLE_EVERY = 0.05
TONE_HZ = 440

results: list[bool] = []

type Sample = tuple[int, int, int, int]


def report(step: str, passed: bool) -> None:
    results.append(passed)
    print(f"{'PASS' if passed else 'FAIL'}  {step}", flush=True)


def skip(step: str, why: str) -> None:
    print(f"SKIP  {step}: {why}", flush=True)


async def ask(question: str) -> bool:
    answer = await asyncio.to_thread(input, f"\n{question} [y/n] ")
    return answer.strip().lower().startswith("y")


def tone(seconds: float) -> bytes:
    samples = (
        int(8000 * math.sin(2 * math.pi * TONE_HZ * index / 48_000))
        for index in range(int(48_000 * seconds))
    )
    return b"".join(struct.pack("<hh", sample, sample) for sample in samples)


class Warnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


async def connect(bot: Bot, token: str) -> "asyncio.Task[None]":
    started = asyncio.create_task(bot.start(token))
    ready = asyncio.ensure_future(bot.wait_until_ready())
    await asyncio.wait(
        [started, ready],
        timeout=GATEWAY_TIMEOUT,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if started.done():
        started.result()
    report("connect", ready.done())
    ready.cancel()
    return started


async def within[T](seconds: float, waiting: "asyncio.Future[T]") -> T | None:
    await asyncio.wait([waiting], timeout=seconds)
    if not waiting.done():
        waiting.cancel()
        return None
    return waiting.result()


def first_index(
    timeline: list[voice.VoiceEvent],
    start: int,
    match: Callable[[voice.VoiceEvent], bool],
) -> int | None:
    return next(
        (index for index in range(start, len(timeline)) if match(timeline[index])),
        None,
    )


def dave_transition(event: voice.VoiceEvent) -> bool:
    return isinstance(event, voice.DaveTransition)


def ready(event: voice.VoiceEvent) -> bool:
    return isinstance(event, voice.StateChange) and event.new is State.READY


def became(
    state: State, detail: str | None = None
) -> Callable[[voice.VoiceEvent], bool]:
    return lambda event: (
        isinstance(event, voice.StateChange)
        and event.new is state
        and (detail is None or event.detail == detail)
    )


@contextlib.asynccontextmanager
async def sampled(bot: Bot, guild_id: int) -> AsyncIterator[list[list[Sample]]]:
    runs: list[list[Sample]] = []

    async def sample() -> None:
        current: voice.VoiceConnection | None = None
        while True:
            vc = bot.voice.get(guild_id)
            if vc is not None and vc.state is not State.DISCONNECTED:
                if vc is not current:
                    current = vc
                    runs.append([])
                send = vc.stats().send
                runs[-1].append(
                    (
                        send.recoveries,
                        send.transitions_executed,
                        send.packets_sent,
                        send.held_ticks,
                    )
                )
            await asyncio.sleep(SAMPLE_EVERY)

    sampler = asyncio.create_task(sample())
    try:
        yield runs
    finally:
        sampler.cancel()


def recovery_hold(runs: list[list[Sample]]) -> bool | None:
    for samples in runs:
        for index in range(1, len(samples)):
            recoveries, transitions, _, _ = samples[index]
            if recoveries > samples[index - 1][0]:
                window = [s for s in samples[index:] if s[1] == transitions]
                quiet = window[-1][2] == window[0][2]
                held = window[-1][3] > window[0][3]
                return None if quiet and not held else quiet
    return None


async def play(vc: voice.VoiceConnection, seconds: float) -> None:
    async with vc.sink() as out:
        await out.write(tone(seconds))


async def voice_check(
    token: str,
    guild_id: int,
    channel_id: int,
    second_id: int | None,
    stage_id: int | None,
) -> None:
    bot = Bot(sync_commands=False)
    timeline: list[voice.VoiceEvent] = []
    warnings = Warnings()
    logging.getLogger("bostie.voice").addHandler(warnings)

    @bot.listen
    async def record(
        event: voice.StateChange
        | voice.Moved
        | voice.UserJoin
        | voice.UserLeave
        | voice.DaveTransition
        | voice.MediaStalled
        | voice.MediaResumed,
    ) -> None:
        timeline.append(event)

    started = await connect(bot, token)
    async with sampled(bot, guild_id) as runs:
        vc = await bot.voice.connect(channel_id, timeout=VOICE_TIMEOUT)
        report(
            "connect to the empty channel: READY with DAVE protocol 1",
            vc.state is State.READY and vc.dave.protocol_version == 1,
        )
        early = first_index(timeline, 0, lambda e: isinstance(e, voice.UserJoin))
        print(f"op 11 before READY while alone: {'yes' if early is not None else 'no'}")
        before = vc.stats().send
        await play(vc, 1.0)
        after = vc.stats().send
        report(
            "a 1 s write drains while alone and sends nothing",
            after.alone_ticks > before.alone_ticks
            and after.packets_sent == before.packets_sent,
        )
        report(
            "no stall reported while alone",
            not any(isinstance(e, voice.MediaStalled) for e in timeline),
        )

        print(
            f"\nJoin the voice channel {channel_id} within {HUMAN_TIMEOUT} s.",
            flush=True,
        )
        mark = len(timeline)
        joined = await within(
            HUMAN_TIMEOUT, asyncio.ensure_future(bot.wait_for(voice.UserJoin))
        )
        if joined is None:
            report("a human joins: UserJoin, then DaveTransition", False)
            await bot.close()
            await started
            return
        if first_index(timeline, mark, dave_transition) is None:
            await within(
                VOICE_TIMEOUT, asyncio.ensure_future(bot.wait_for(voice.DaveTransition))
            )
        report(
            "a human joins: UserJoin, then DaveTransition",
            first_index(timeline, mark, dave_transition) is not None
            and vc.dave.privacy_code is not None,
        )
        human = joined.user_id
        print(f"privacy code: {vc.dave.privacy_code}")
        print(
            f"verification code for {human}: {await vc.dave.verification_code(human)}"
        )
        print("Compare both with the call's connection details in the Discord client.")

        await vc.disconnect()
        mark = len(timeline)
        vc = await bot.voice.connect(channel_id, timeout=VOICE_TIMEOUT)
        await asyncio.sleep(1)
        greeted = first_index(
            timeline,
            mark,
            lambda e: isinstance(e, voice.UserJoin) and e.user_id == human,
        )
        readied = first_index(timeline, mark, ready)
        if greeted is None or readied is None:
            print("op 11 order with a human present: no op 11 seen")
        else:
            print(
                "op 11 arrived "
                f"{'before' if greeted < readied else 'after'} the session description "
                "(a human was already in the channel)"
            )

        before = vc.stats().send
        await play(vc, 5.0)
        after = vc.stats().send
        heard = await ask("Did you hear a 5 s tone?")
        report(
            "a 5 s PCM tone is heard",
            heard
            and after.packets_sent - before.packets_sent >= 245
            and after.dave_not_ready == before.dave_not_ready,
        )

        encoder = voice.OpusEncoder()
        before = vc.stats().send
        async with vc.sink(voice.Opus()) as out:
            for packet in encoder.encode(tone(3.0)) + encoder.flush():
                await out.write(packet)
        after = vc.stats().send
        heard = await ask("Did you hear a 3 s tone again?")
        report(
            "Opus passthrough is heard",
            heard and after.passthrough_packets > before.passthrough_packets,
        )

        before = vc.stats().send
        mark = len(timeline)
        playing = asyncio.create_task(play(vc, 4.0))
        await asyncio.sleep(1.5)
        vc.reconnect()
        await playing
        await within(VOICE_TIMEOUT, asyncio.ensure_future(vc.wait_until(State.READY)))
        after = vc.stats().send
        heard = await ask("Did the last 4 s tone play without a gap?")
        report(
            "vc.reconnect() mid-tone resumes without a new identify or a gap",
            heard
            and first_index(timeline, mark, became(State.RESUMING)) is not None
            and after.resumes == before.resumes + 1
            and after.rejoins == before.rejoins,
        )

        mark = len(timeline)
        server = asyncio.ensure_future(
            bot.wait_for(
                VoiceServerUpdate, check=lambda event: event.guild_id == guild_id
            )
        )
        bot.gateway.send(
            4,
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
                "self_mute": vc.self_mute,
                "self_deaf": vc.self_deaf,
            },
        )
        await within(VOICE_TIMEOUT, server)
        await asyncio.sleep(1)
        await within(VOICE_TIMEOUT, asyncio.ensure_future(vc.wait_until(State.READY)))
        reconnecting = first_index(
            timeline, mark, became(State.RECONNECTING, "server_changed")
        )
        report(
            "a forced rejoin goes RECONNECTING (server_changed) and back to READY",
            reconnecting is not None
            and first_index(timeline, reconnecting, ready) is not None,
        )

        send = vc.stats().send
        report(
            "UDP keepalives are answered and the UDP latency is measured",
            send.keepalives_answered > 0 and vc.latency.udp is not None,
        )

        if second_id is None:
            skip("move and move back", "set BOSTIE_LIVE_VOICE_2")
        else:
            mark = len(timeline)
            playing = asyncio.create_task(play(vc, 6.0))
            await vc.move(second_id)
            print(
                f"Moved to {second_id}; follow the bot there to keep listening.",
                flush=True,
            )
            await asyncio.sleep(2)
            await vc.move(channel_id)
            await playing
            moves = [e for e in timeline[mark:] if isinstance(e, voice.Moved)]
            heard = await ask("Did the tone continue across both moves?")
            report(
                "move and move back: two Moved(by_us=True), READY each time",
                heard
                and [(m.new_channel_id, m.by_us) for m in moves]
                == [(second_id, True), (channel_id, True)]
                and vc.state is State.READY,
            )

        hold = recovery_hold(runs)
        if hold is None:
            skip(
                "DAVE recovery hold",
                "no DAVE recovery ran; Discord starts one only after an invalid "
                "commit or welcome, which the bot cannot cause",
            )
        else:
            report("DAVE recovery holds media until the re-add transition", hold)

        if stage_id is None:
            skip("stage listen and request", "set BOSTIE_LIVE_STAGE")
        else:
            await bot.voice.connect(stage_id)
            warnings.messages.clear()
            await play(vc, 0.2)
            await play(vc, 0.2)
            report(
                "a stage in listen mode: no DAVE, suppressed, one warning on audio",
                vc.dave.protocol_version == 0
                and vc.suppress
                and sum("suppressed" in m for m in warnings.messages) == 1,
            )
            await vc.stage("request")
            state = await bot.http.request(
                "GET", "/guilds/{guild_id}/voice-states/@me", guild_id=guild_id
            )
            report(
                "vc.stage('request') sets request_to_speak_timestamp",
                state.get("request_to_speak_timestamp") is not None,
            )
            print(f"stage SSRC: {vc.diagnostics().get('ssrc')}")

        ended = asyncio.ensure_future(
            bot.wait_for(voice.StateChange, check=became(State.DISCONNECTED))
        )
        await vc.disconnect()
        change = await within(VOICE_TIMEOUT, ended)
        left = await ask("Did the bot leave the channel in the client?")
        report(
            "disconnect: DISCONNECTED (requested) and the bot leaves",
            left
            and change is not None
            and change.reason is voice.DisconnectReason.REQUESTED,
        )
        print(f"recoveries in this run: {sum(run[-1][0] for run in runs if run)}")

    await bot.close()
    await started


def optional(name: str) -> int | None:
    value = os.environ.get(name)
    return int(value) if value else None


def main() -> None:
    missing = [name for name in VARIABLES if not os.environ.get(name)]
    if missing:
        sys.exit(f"the voice check needs {', '.join(missing)}; see CONTRIBUTING.md")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        voice_check(
            os.environ["BOSTIE_TOKEN"],
            int(os.environ["BOSTIE_LIVE_GUILD"]),
            int(os.environ["BOSTIE_LIVE_VOICE"]),
            optional("BOSTIE_LIVE_VOICE_2"),
            optional("BOSTIE_LIVE_STAGE"),
        )
    )
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
