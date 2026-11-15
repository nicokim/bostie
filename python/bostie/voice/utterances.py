import asyncio
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bostie.voice.events import SpeechStart, SpeechStop, UserLeave
from bostie.voice.formats import PCM, SAMPLE_BYTES, SAMPLE_RATE
from bostie.voice.frames import Frame

if TYPE_CHECKING:
    from bostie.voice.receiver import Receiver


@dataclass(frozen=True, slots=True)
class Utterance:
    """One user's speech from start to end, in the receiver's PCM format."""

    user_id: int
    data: bytes
    offset: int
    duration: float
    lost: float


@dataclass(slots=True)
class _Speech:
    offset: int
    end: int
    data: bytearray = field(default_factory=bytearray)
    lost: int = 0
    stop: int | None = None
    deadline: float = math.inf

    def utterance(self, user_id: int) -> Utterance:
        return Utterance(
            user_id,
            bytes(self.data),
            self.offset,
            (self.end - self.offset) / SAMPLE_RATE,
            self.lost / SAMPLE_RATE,
        )


def utterances(
    rx: "Receiver[Frame | SpeechStart | SpeechStop | UserLeave]",
    *,
    min_silence: float = 0.8,
    max_length: float = 30.0,
    level: float | None = None,
) -> AsyncIterator[Utterance]:
    """Group a receiver's frames into utterances per user.

    With `level`, speech is audio louder than `level` dBFS, not the speech markers.
    """
    format = rx.format
    if not isinstance(format, PCM) or rx.mix or not (rx.markers or level is not None):
        raise ValueError(
            "utterances needs a PCM receiver with no mix, opened with markers=True "
            "or given a level"
        )
    grouping = _Grouping(
        silence=round(min_silence * SAMPLE_RATE),
        longest=round(max_length * SAMPLE_RATE),
        pause=rx.jitter + min_silence,
        width=format.channels * SAMPLE_BYTES[format.sample],
        rate=format.rate,
        level=level,
    )
    return _utterances(rx, grouping)


async def _utterances(
    rx: "Receiver[Frame | SpeechStart | SpeechStop | UserLeave]", grouping: "_Grouping"
) -> AsyncIterator[Utterance]:
    while True:
        deadline = grouping.deadline()
        try:
            item = await _next_before(rx, deadline)
        except StopAsyncIteration:
            break
        for utterance in grouping.take(item, deadline):
            yield utterance
    for user, speech in grouping.speaking.items():
        yield speech.utterance(user)


async def _next_before(
    rx: "Receiver[Frame | SpeechStart | SpeechStop | UserLeave]", deadline: float
) -> Frame | SpeechStart | SpeechStop | UserLeave | None:
    try:
        async with asyncio.timeout(
            None if math.isinf(deadline) else deadline - time.monotonic()
        ):
            return await anext(rx)
    except TimeoutError:
        return None


@dataclass(slots=True)
class _Grouping:
    silence: int
    longest: int
    pause: float
    width: int
    rate: int
    level: float | None
    speaking: dict[int, _Speech] = field(default_factory=dict[int, _Speech])

    def deadline(self) -> float:
        return min(
            (speech.deadline for speech in self.speaking.values()), default=math.inf
        )

    def take(
        self, item: Frame | SpeechStart | SpeechStop | UserLeave | None, deadline: float
    ) -> list[Utterance]:
        match item:
            case None:
                return self._expire(deadline)
            case Frame(user_id=int(user)) if self._quiet(item):
                return self._pause(user, item)
            case Frame(user_id=int(user)):
                return self._add(user, item)
            case UserLeave(user_id=user) if user in self.speaking:
                return [self.speaking.pop(user).utterance(user)]
            case _ if self.level is not None:
                pass
            case SpeechStart(user_id=user, offset=int(offset)):
                return self._start(user, offset)
            case SpeechStop(user_id=user, offset=int(offset), at=at):
                self._stop(user, offset, at)
            case _:
                pass
        return []

    def _quiet(self, frame: Frame) -> bool:
        return self.level is not None and (
            frame.level is None or frame.level < self.level
        )

    def _pause(self, user: int, frame: Frame) -> list[Utterance]:
        speech = self.speaking.get(user)
        if speech is None or frame.offset + frame.samples - speech.end < self.silence:
            return []
        return [self.speaking.pop(user).utterance(user)]

    def _expire(self, deadline: float) -> list[Utterance]:
        expired = [
            user
            for user, speech in self.speaking.items()
            if speech.deadline <= deadline
        ]
        return [self.speaking.pop(user).utterance(user) for user in expired]

    def _start(self, user: int, offset: int) -> list[Utterance]:
        speech = self.speaking.get(user)
        if (
            speech is not None
            and speech.stop is not None
            and offset - speech.stop < self.silence
        ):
            speech.stop, speech.deadline = None, math.inf
            return []
        self.speaking[user] = _Speech(offset, offset)
        return [] if speech is None else [speech.utterance(user)]

    def _stop(self, user: int, offset: int, at: float) -> None:
        speech = self.speaking.get(user)
        if speech is not None:
            speech.stop = offset
            speech.deadline = at + self.pause

    def _add(self, user: int, frame: Frame) -> list[Utterance]:
        ended: list[Utterance] = []
        speech = self.speaking.get(user)
        if speech is not None and (
            frame.offset - speech.end >= self.silence
            or frame.offset + frame.samples - speech.offset > self.longest
        ):
            ended.append(self.speaking.pop(user).utterance(user))
            speech = None
        if speech is None:
            speech = self.speaking[user] = _Speech(frame.offset, frame.offset)
        gap = max(frame.offset - speech.end, 0) * self.rate // SAMPLE_RATE
        speech.data += bytes(gap * self.width)
        speech.data += frame.data
        if self.level is not None:
            speech.deadline = frame.received_at + self.pause
        speech.end = frame.offset + frame.samples
        if frame.lost:
            speech.lost += frame.samples
        return ended
