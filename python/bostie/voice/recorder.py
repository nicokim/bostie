import asyncio
import logging
import math
import os
import string
import wave
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Any, BinaryIO, Self

from bostie.errors import Empty, ReceiverClosed
from bostie.voice._ogg import END_OF_STREAM, MAX_PACKET, OggStream
from bostie.voice.encoder import OpusEncoder
from bostie.voice.formats import DEFAULT_PCM, PCM, SAMPLE_BYTES, SAMPLE_RATE, Opus
from bostie.voice.frames import Frame

if TYPE_CHECKING:
    from bostie.voice.connection import VoiceConnection
    from bostie.voice.receiver import Receiver, ReceiverStats

log = logging.getLogger("bostie.voice")

SILENCE = b"\xf8\xff\xfe"
SAMPLES_PER_PACKET = 960
TEMPLATE_FIELDS = frozenset({"guild_id", "user_id"})


class _OggFile:
    def __init__(self, file: BinaryIO, serial: int, *, encode: bool) -> None:
        self.file = file
        self.stream = OggStream(serial)
        self.encoder = OpusEncoder() if encode else None

    def add(self, frame: Frame, *, align: bool) -> bytes:
        stream = self.stream
        if self.encoder is not None:
            for packet in self.encoder.encode(frame.data):
                stream.add(packet, SAMPLES_PER_PACKET)
        elif not frame.lost and len(frame.data) <= MAX_PACKET:
            if align:
                gap = round((frame.offset - stream.granule) / SAMPLES_PER_PACKET)
                stream.add(SILENCE, SAMPLES_PER_PACKET, max(gap, 0))
            stream.add(frame.data, frame.samples)
        return stream.take()

    def write(self, data: bytes) -> None:
        self.file.write(data)
        self.file.flush()

    def close(self) -> None:
        with self.file:
            for packet in [] if self.encoder is None else self.encoder.flush():
                self.stream.add(packet, SAMPLES_PER_PACKET)
            self.stream.flush(END_OF_STREAM)
            self.file.write(self.stream.take())


class _WavFile:
    def __init__(self, file: BinaryIO, format: PCM) -> None:
        self.file = file
        self.width = format.channels * SAMPLE_BYTES[format.sample]
        self.rate = format.rate
        self.end = 0
        self.wav = wave.Wave_write(file)
        self.wav.setnchannels(format.channels)
        self.wav.setsampwidth(SAMPLE_BYTES[format.sample])
        self.wav.setframerate(format.rate)

    def add(self, frame: Frame, *, align: bool) -> bytes:
        gap = max(frame.offset - self.end, 0) if align else 0
        self.end = frame.offset + frame.samples
        return bytes(gap * self.rate // SAMPLE_RATE * self.width) + frame.data

    def write(self, data: bytes) -> None:
        self.wav.writeframesraw(data)
        self.file.flush()

    def close(self) -> None:
        with self.file:
            self.wav.close()


class Recorder:
    """Record each user, or with `mix=True` the whole call, to Ogg Opus or WAV.

    The path's extension picks the container; `format` is the WAV sample format.
    It ends on its own after `max_duration` seconds or once its files hold
    `max_size` bytes.
    """

    def __init__(
        self,
        vc: "VoiceConnection",
        path: str | os.PathLike[str],
        *,
        users: Iterable[int] | None = None,
        exclude: Iterable[int] = (),
        include_self: bool = False,
        align: bool = True,
        mix: bool = False,
        format: PCM = DEFAULT_PCM,
        buffer: float = 30.0,
        jitter: float = 0.3,
        max_duration: float | None = None,
        max_size: int | None = None,
    ) -> None:
        if max_duration is not None and not (
            math.isfinite(max_duration) and max_duration > 0
        ):
            raise ValueError(
                f"max_duration must be a positive number of seconds, got {max_duration}"
            )
        if max_size is not None and max_size <= 0:
            raise ValueError(f"max_size must be a positive byte count, got {max_size}")
        self._template = _template(path, mix=mix)
        self._wav = self._template.endswith(".wav")
        if self._wav and format.sample != "s16le":
            raise ValueError(f"a WAV recording takes s16le samples, got {format}")
        self._format = format if self._wav else DEFAULT_PCM
        self._guild_id = vc.guild_id
        self._align = align
        self._mix = mix
        self._max_duration = max_duration
        self._end = math.inf if max_duration is None else max_duration * SAMPLE_RATE
        self._room = math.inf if max_size is None else max_size
        self._files: dict[int | None, Path] = {}
        self._writers: dict[int | None, _OggFile | _WavFile] = {}
        self._error: Exception | None = None
        self._stopping: asyncio.Future[None] = (
            asyncio.get_running_loop().create_future()
        )
        self._rx: Receiver[Frame] = vc.receive(
            self._format if self._wav or mix else Opus(),
            users=users,
            exclude=exclude,
            include_self=include_self,
            buffer=buffer,
            jitter=jitter,
            stall="wait",
            mix=mix,
        )
        self._task = asyncio.create_task(self._record())

    @property
    def files(self) -> Mapping[int | None, Path]:
        """The files written so far, by user id; None keys a mixed recording."""
        return MappingProxyType(self._files)

    def stats(self) -> "ReceiverStats":
        """The receiver's counters; dropped means the disk fell behind for too long."""
        return self._rx.stats()

    async def stop(self) -> dict[int | None, Path]:
        """Stop once heard audio is queued or the wait gives up; return the files."""
        await self._halt()
        return await self.wait()

    async def wait(self) -> dict[int | None, Path]:
        """Wait until the recording ends on its own, then return the files."""
        await asyncio.wait({self._task})
        if self._error is not None:
            raise self._error
        return dict(self._files)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.stop()

    async def _record(self) -> None:
        limit = (
            None
            if self._max_duration is None
            else asyncio.create_task(self._halt_after(self._max_duration))
        )
        try:
            await self._consume()
        except Exception as error:
            self._fail(error)
        finally:
            if limit is not None:
                limit.cancel()
            self._rx.close()
            await self._finish()

    async def _halt_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self._halt()

    async def _halt(self) -> None:
        await self._rx.mark()
        if not self._stopping.done():
            self._stopping.set_result(None)

    async def _consume(self) -> None:
        while self._room > 0:
            try:
                frame = self._rx.get_nowait()
            except ReceiverClosed:
                return
            except Empty:
                if self._stopping.done():
                    return
                arrived = await self._next()
                if arrived is None:
                    continue
                frame = arrived
            await self._write(frame)

    async def _next(self) -> Frame | None:
        reading = asyncio.create_task(self._read())
        either: set[asyncio.Future[Any]] = {reading, self._stopping}
        try:
            await asyncio.wait(either, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if reading.cancel():
                await asyncio.wait({reading})
        if reading.cancelled() or isinstance(reading.exception(), StopAsyncIteration):
            return None
        return reading.result()

    async def _read(self) -> Frame:
        return await anext(self._rx)

    async def _write(self, frame: Frame) -> None:
        user = frame.user_id
        if (user is None and not self._mix) or frame.offset + frame.samples > self._end:
            return
        writer = self._writers.get(user)
        if writer is None:
            fields = {"guild_id": self._guild_id, "user_id": user}
            path = Path(self._template.format(**fields))
            file = await asyncio.to_thread(_create, path)
            writer = self._writers[user] = (
                _WavFile(file, self._format)
                if self._wav
                else _OggFile(file, (user or 0) & 0xFFFFFFFF, encode=self._mix)
            )
            self._files[user] = path
        data = writer.add(frame, align=self._align)
        if data:
            await asyncio.to_thread(writer.write, data)
            self._room -= len(data)

    async def _finish(self) -> None:
        for writer in self._writers.values():
            try:
                await asyncio.to_thread(writer.close)
            except Exception as error:
                self._fail(error)

    def _fail(self, error: Exception) -> None:
        if self._error is None:
            self._error = error
            log.error("recording to %s failed", self._template, exc_info=error)


def _template(path: str | os.PathLike[str], *, mix: bool) -> str:
    template = os.fspath(path)
    if not template.endswith((".ogg", ".opus", ".wav")):
        raise ValueError(f"path must end in .ogg, .opus or .wav, got {template!r}")
    fields = {
        name for _, name, _, _ in string.Formatter().parse(template) if name is not None
    }
    if mix and "user_id" in fields:
        raise ValueError(
            f"path of a mixed recording names one file, without {{user_id}}, "
            f"got {template!r}"
        )
    if not mix and "user_id" not in fields:
        raise ValueError(
            f"path must contain {{user_id}} to give each user a file, got {template!r}"
        )
    if not fields <= TEMPLATE_FIELDS:
        raise ValueError(
            f"path may use only {{guild_id}} and {{user_id}}, got {template!r}"
        )
    template.format(guild_id=0, user_id=0)
    return template


def _create(path: Path) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("wb")
