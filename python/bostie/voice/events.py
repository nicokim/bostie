from typing import ClassVar, dataclass_transform

import msgspec

from bostie.enums import (
    Direction,
    DisconnectReason,
    Intents,
    SpeakingFlags,
    State,
)


@dataclass_transform(kw_only_default=True, frozen_default=True)
class VoiceEvent(msgspec.Struct, frozen=True, kw_only=True, gc=False):
    """Base of the events a voice connection emits."""

    NAME: ClassVar[str]
    INTENTS: ClassVar[Intents] = Intents(0)

    guild_id: int


class StateChange(VoiceEvent, kw_only=True):
    NAME = "voice.state_change"

    old: State
    new: State
    reason: DisconnectReason | None
    code: int | None
    detail: str


class Moved(VoiceEvent, kw_only=True):
    NAME = "voice.moved"

    old_channel_id: int
    new_channel_id: int
    by_us: bool


class DaveTransition(VoiceEvent, kw_only=True):
    NAME = "voice.dave_transition"

    protocol_version: int
    epoch: int | None
    transition_id: int


class MediaStalled(VoiceEvent, kw_only=True):
    NAME = "voice.media_stalled"

    direction: Direction
    duration: float


class MediaResumed(VoiceEvent, kw_only=True):
    NAME = "voice.media_resumed"

    direction: Direction
    duration: float


class SpeakingUpdate(VoiceEvent, kw_only=True):
    NAME = "voice.speaking_update"

    user_id: int
    ssrc: int
    flags: SpeakingFlags


class UserJoin(VoiceEvent, kw_only=True):
    NAME = "voice.user_join"

    user_id: int


class UserLeave(VoiceEvent, kw_only=True):
    """A user left the call; in a receiver, `offset` is where on its clock."""

    NAME = "voice.user_leave"

    user_id: int
    offset: int | None = None


class SpeechStart(VoiceEvent, kw_only=True):
    """A user started speaking."""

    NAME = "voice.speech_start"

    user_id: int
    ssrc: int
    at: float
    offset: int | None = None


class SpeechStop(VoiceEvent, kw_only=True):
    """A user stopped speaking."""

    NAME = "voice.speech_stop"

    user_id: int
    ssrc: int
    at: float
    duration: float
    offset: int | None = None


LOCAL_NAMES = frozenset(
    event.NAME
    for event in (
        StateChange,
        Moved,
        DaveTransition,
        MediaStalled,
        MediaResumed,
        SpeakingUpdate,
        UserJoin,
        UserLeave,
        SpeechStart,
        SpeechStop,
    )
)
