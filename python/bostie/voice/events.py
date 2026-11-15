from typing import TYPE_CHECKING, ClassVar, Self, dataclass_transform

import msgspec

from bostie.enums import (
    Direction,
    DisconnectReason,
    Intents,
    SpeakingFlags,
    State,
)
from bostie.voice.formats import _SAMPLE_RATE

if TYPE_CHECKING:
    from bostie._native import RtcpPacketItem

_DELAY_UNITS = 65_536


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
    """A user left the call; from a receiver, `offset` is the time on its clock."""

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


class ReceptionReport(msgspec.Struct, frozen=True, gc=False):
    """How one stream reaches whoever sent the report, as RFC 3550 measures it."""

    ssrc: int
    user_id: int | None
    fraction_lost: float
    lost: int
    highest_sequence: int
    jitter: float
    last_sender_report: int
    delay: float


class RtcpPacket(VoiceEvent, kw_only=True):
    """An RTCP packet from the voice server, decrypted, with its reports read."""

    NAME = "voice.rtcp_packet"

    packet_type: int
    count: int
    ssrc: int
    user_id: int | None
    reports: tuple[ReceptionReport, ...]
    data: bytes

    @classmethod
    def _decode(cls, guild_id: int, packet: "RtcpPacketItem") -> Self:
        kind, count, ssrc, user_id, blocks, data = packet
        reports = tuple(
            ReceptionReport(
                ssrc,
                user,
                fraction / 256,
                lost,
                highest,
                jitter / _SAMPLE_RATE,
                last,
                delay / _DELAY_UNITS,
            )
            for ssrc, user, fraction, lost, highest, jitter, last, delay in blocks
        )
        return cls(
            guild_id=guild_id,
            packet_type=kind,
            count=count,
            ssrc=ssrc,
            user_id=user_id,
            reports=reports,
            data=data,
        )
