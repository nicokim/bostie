import asyncio
import contextlib
import itertools
import logging
import math
import os
import struct
import sys
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path

from bostie import Bot, voice
from bostie.events import VoiceServerUpdate
from bostie.voice import State

VARIABLES = ("BOSTIE_TOKEN", "BOSTIE_LIVE_GUILD", "BOSTIE_LIVE_VOICE")
HUMAN_TIMEOUT = 120
GATEWAY_TIMEOUT = 30
VOICE_TIMEOUT = 15
SAMPLE_EVERY = 0.05
TONE_HZ = 440
SILENCE = b"\xf8\xff\xfe"
SOAK_MINUTES = 30
LISTEN = voice.PCM(16_000, 1)

results: list[bool] = []

type Sample = tuple[int, int, int, int]
type SpeechLog = list[
    tuple[float, voice.SpeakingUpdate | voice.SpeechStart | voice.SpeechStop]
]


def report(step: str, passed: bool) -> None:
    results.append(passed)
    print(f"{'PASS' if passed else 'FAIL'}  {step}", flush=True)


def skip(step: str, why: str) -> None:
    print(f"SKIP  {step}: {why}", flush=True)


async def ask(question: str) -> bool:
    answer = await asyncio.to_thread(input, f"\n{question} [y/n] ")
    return answer.strip().lower().startswith("y")


async def prompt(instruction: str) -> None:
    await asyncio.to_thread(input, f"\n{instruction} Press Enter to start. ")


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


async def switch_region(
    bot: Bot, guild_id: int, channel_id: int, region: str | None
) -> None:
    server = asyncio.ensure_future(
        bot.wait_for(VoiceServerUpdate, check=lambda event: event.guild_id == guild_id)
    )
    await bot.http.request(
        "PATCH",
        "/channels/{channel_id}",
        channel_id=channel_id,
        json={"rtc_region": region},
        reason="bostie voice check",
    )
    await within(VOICE_TIMEOUT, server)
    await asyncio.sleep(1)


async def play(vc: voice.VoiceConnection, seconds: float) -> None:
    async with vc.sink() as out:
        await out.write(tone(seconds))


async def beeps(vc: voice.VoiceConnection, count: int) -> None:
    pause = bytes(48_000 * 4 * 9 // 5)
    async with vc.sink() as out:
        await out.write((tone(0.2) + pause) * count)


async def collect[T: voice.Frame | voice.Packet | voice.SpeechStart | voice.SpeechStop](
    rx: voice.Receiver[T], seconds: float
) -> list[T]:
    items: list[T] = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(seconds):
            async for item in rx:
                items.append(item)
    return items


def spoken(items: Iterable[object]) -> int:
    return sum(isinstance(item, voice.Frame) and not item.lost for item in items)


def print_op5_timing(
    items: Iterable[voice.Frame | voice.Packet], speech: SpeechLog
) -> None:
    firsts: dict[int, float] = {}
    for item in items:
        if item.ssrc is not None:
            firsts.setdefault(item.ssrc, item.received_at)
    for ssrc, first in firsts.items():
        ops = [
            f"{at - first:+.3f} s"
            for at, event in speech
            if isinstance(event, voice.SpeakingUpdate) and event.ssrc == ssrc
        ]
        print(f"SSRC {ssrc}: op 5 at {', '.join(ops) or 'never'} from its first packet")


async def receive_checks(
    bot: Bot,
    vc: voice.VoiceConnection,
    human: int,
    warnings: Warnings,
    speech: SpeechLog,
) -> None:
    await prompt("Talk for 10 s, with a pause or two.")
    async with (
        vc.receive(LISTEN, markers=True) as rx,
        vc.receive(voice.Packets()) as raw,
    ):
        items, packets = await asyncio.gather(collect(rx, 12), collect(raw, 12))
    after = vc.stats().receive
    frames = [i for i in items if isinstance(i, voice.Frame) and i.user_id == human]
    marks = [i for i in items if isinstance(i, voice.SpeechStart | voice.SpeechStop)]
    for mark in marks:
        length = (
            f", {mark.duration:.2f} s" if isinstance(mark, voice.SpeechStop) else ""
        )
        print(f"{type(mark).__name__} {mark.user_id} at offset {mark.offset}{length}")
    print_op5_timing(packets, speech)
    steps = Counter(
        (b.timestamp - a.timestamp) % 2**32 // 48
        for a, b in itertools.pairwise(p for p in packets if p.user_id == human)
        if (b.sequence - a.sequence) % 2**16 == 1
    )
    print(
        "packet durations, from the timestamp step per sequence step "
        f"(ms: count, over 120 is a pause): {dict(sorted(steps.items()))}"
    )
    sizes = Counter(min(len(packet.opus), 16) for packet in packets)
    print(f"Opus sizes (bytes, 16 = 16 or more: count): {dict(sorted(sizes.items()))}")
    report(
        "10 s of speech: frames under the speaker's id, SpeechStart and SpeechStop",
        spoken(frames) > 0
        and any(isinstance(m, voice.SpeechStart) and m.user_id == human for m in marks)
        and any(isinstance(m, voice.SpeechStop) and m.user_id == human for m in marks),
    )
    extensions = Counter(
        (number, len(body)) for packet in packets for number, body in packet.extensions
    )
    print(f"header extensions ((id, bytes): count): {dict(sorted(extensions.items()))}")
    print(f"RTCP sender reports so far: {after.rtcp_sender_reports}")
    print(
        "padding, CSRC count, marker bit and nonce layout: not visible through the "
        "library; a packet capture shows them, since they travel unencrypted"
    )
    report(
        "a Packets() receiver: the speaker's packets arrive and no replay is refused",
        any(packet.user_id == human for packet in packets) and after.replays == 0,
    )

    await prompt(
        "Within 15 s: talk, mute yourself mid-sentence, wait 3 s, unmute, talk again."
    )
    start = len(speech)
    async with vc.receive(voice.Packets(), users={human}) as raw:
        packets = await collect(raw, 15)
    runs = [
        len(list(run))
        for silent, run in itertools.groupby(packets, lambda p: p.opus == SILENCE)
        if silent
    ]
    print(f"F8 FF FE runs (packets each): {runs}")
    lags = [
        at - event.at
        for at, event in speech[start:]
        if isinstance(event, voice.SpeechStop) and event.user_id == human
    ]
    print(f"SpeechStop after the last speech packet: {[round(lag, 3) for lag in lags]}")
    report(
        "muting ends speech within the hangover",
        bool(lags) and max(lags) <= vc.speech_hangover + 0.1,
    )

    if not await ask("Can you turn your camera on in this channel?"):
        skip("a camera turned on", "no camera")
    else:
        await prompt("Turn your camera on and talk for 10 s.")
        async with vc.receive(LISTEN, users={human}) as rx:
            frames = await collect(rx, 10)
        print("Turn your camera off.")
        report(
            "a camera turned on: no invalid payloads and frames keep arriving",
            vc.stats().receive.invalid_payloads == 0 and spoken(frames) > 0,
        )

    if not await ask("Can a second person join and leave while you talk?"):
        skip("a second person joins and leaves", "nobody else")
    else:
        await prompt(
            "Keep talking while the second person joins, stays 5 s and leaves."
        )
        async with vc.receive(LISTEN, users={human}) as rx:
            listening = asyncio.create_task(collect(rx, 3 * HUMAN_TIMEOUT))
            joined = await within(
                HUMAN_TIMEOUT,
                asyncio.ensure_future(
                    bot.wait_for(voice.UserJoin, check=lambda e: e.user_id != human)
                ),
            )
            left = None
            if joined is not None:
                left = await within(
                    HUMAN_TIMEOUT,
                    asyncio.ensure_future(
                        bot.wait_for(
                            voice.UserLeave, check=lambda e: e.user_id == joined.user_id
                        )
                    ),
                )
            await asyncio.sleep(2)
            rx.close()
            frames = await listening
        stats = vc.stats()
        for user_id, counters in stats.users.items():
            print(f"user {user_id}: {counters.decrypt_failures} decrypt failures")
        report(
            "a second person joins and leaves: frames go on, no receive recovery",
            left is not None
            and spoken(frames) > 0
            and stats.receive.receive_recoveries == 0,
        )

    me = bot.user.id if bot.user is not None else 0
    folder = Path(tempfile.mkdtemp(prefix="bostie-recording-"))
    await prompt("For 20 s, say 'now' each time you hear a beep.")
    recorder = voice.Recorder(vc, folder / "{user_id}.ogg", include_self=True)
    await beeps(vc, 10)
    files = await recorder.stop()
    for user_id, path in files.items():
        print(f"recorded {user_id}: {path}")
    lined_up = await ask(
        "Open both files together in an audio editor. Does each 'now' follow its "
        "beep the same way all through?"
    )
    report(
        "Recorder(include_self=True): one file for the bot, one for you, lined up",
        lined_up and {me, human} <= files.keys(),
    )

    await prompt("Talk for 10 s, until the bot says to stop.")
    async with vc.receive(LISTEN, users={human}) as rx:
        hearing = await collect(rx, 2)
        warnings.messages.clear()
        await vc.edit(self_deaf=True)
        await asyncio.sleep(1)
        rx.clear()
        deafened = await collect(rx, 3)
        warned = sum("deafened" in message for message in warnings.messages)
        await vc.edit(self_deaf=False)
        resumed = await collect(rx, 4)
    print("Stop talking.")
    print(
        f"frames: {spoken(hearing)} before, {spoken(deafened)} deafened, "
        f"{spoken(resumed)} after"
    )
    report(
        "self-deafen with a receiver open: one warning, frames stop, then resume",
        warned == 1
        and spoken(hearing) > 0
        and spoken(deafened) == 0
        and spoken(resumed) > 0,
    )

    ssrcs = {ssrc for ssrc, user_id in vc.ssrcs.items() if user_id == human}
    await prompt("Leave the voice channel, join it again and talk for 5 s.")
    async with vc.receive(LISTEN, users={human}) as rx:
        listening = asyncio.create_task(collect(rx, 3 * HUMAN_TIMEOUT))
        gone = await within(
            HUMAN_TIMEOUT,
            asyncio.ensure_future(
                bot.wait_for(voice.UserLeave, check=lambda e: e.user_id == human)
            ),
        )
        back = None
        if gone is not None:
            back = await within(
                HUMAN_TIMEOUT,
                asyncio.ensure_future(
                    bot.wait_for(voice.UserJoin, check=lambda e: e.user_id == human)
                ),
            )
        rejoined = time.monotonic()
        await asyncio.sleep(8)
        rx.close()
        frames = [
            frame
            for frame in await listening
            if frame.received_at > rejoined and not frame.lost
        ]
    rejoined_ssrcs = sorted({f.ssrc for f in frames if f.ssrc is not None})
    print(f"SSRC before: {sorted(ssrcs)}, after: {rejoined_ssrcs}")
    print_op5_timing(frames, speech)
    report(
        "the speaker leaves and rejoins: frames continue under the same id",
        back is not None and bool(frames),
    )


async def soak(bot: Bot, channel_id: int, human: int) -> None:
    if not os.environ.get("BOSTIE_LIVE_SOAK"):
        skip(f"{SOAK_MINUTES} minutes of receive only", "set BOSTIE_LIVE_SOAK=1")
        return
    vc = await bot.voice.connect(channel_id, timeout=VOICE_TIMEOUT)
    before = vc.stats().send.keepalives_answered
    print(
        f"\nFor {SOAK_MINUTES} minutes the bot only listens. Talk now and then.",
        flush=True,
    )
    async with vc.receive(LISTEN, users={human}) as rx:
        for minute in range(1, SOAK_MINUTES + 1):
            print(
                f"minute {minute}: {spoken(await collect(rx, 60))} frames", flush=True
            )
        await prompt("Talk for 10 s.")
        last = spoken(await collect(rx, 10))
    after = vc.stats().send.keepalives_answered
    report(
        f"{SOAK_MINUTES} minutes of receive only: frames keep arriving, keepalives "
        "are answered",
        last > 0 and after > before,
    )
    await vc.disconnect()


async def voice_check(
    token: str,
    guild_id: int,
    channel_id: int,
    second_id: int | None,
    stage_id: int | None,
) -> None:
    bot = Bot(sync_commands=False)
    timeline: list[voice.VoiceEvent] = []
    speech: SpeechLog = []
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

    @bot.listen
    async def hear(
        event: voice.SpeakingUpdate | voice.SpeechStart | voice.SpeechStop,
    ) -> None:
        speech.append((time.monotonic(), event))

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

        original = (
            await bot.http.request(
                "GET", "/channels/{channel_id}", channel_id=channel_id
            )
        ).get("rtc_region")
        regions = await bot.http.request("GET", "/voice/regions")
        region = next(
            entry["id"]
            for entry in regions
            if not entry["deprecated"] and entry["id"] != original
        )
        mark = len(timeline)
        await switch_region(bot, guild_id, channel_id, region)
        await within(VOICE_TIMEOUT, asyncio.ensure_future(vc.wait_until(State.READY)))
        reconnecting = first_index(timeline, mark, became(State.RECONNECTING))
        if reconnecting is not None:
            interrupted = timeline[reconnecting]
            assert isinstance(interrupted, voice.StateChange)
            print(
                f"region change: {interrupted.detail!r} "
                f"after close code {interrupted.code}"
            )
        report(
            f"a region change to {region} goes RECONNECTING and back to READY",
            reconnecting is not None
            and first_index(timeline, reconnecting, ready) is not None,
        )
        await switch_region(bot, guild_id, channel_id, original)
        await within(VOICE_TIMEOUT, asyncio.ensure_future(vc.wait_until(State.READY)))

        send = vc.stats().send
        report(
            "UDP keepalives are answered and the UDP latency is measured",
            send.keepalives_answered > 0 and vc.latency.udp is not None,
        )

        await receive_checks(bot, vc, human, warnings, speech)

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
        await soak(bot, channel_id, human)

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
